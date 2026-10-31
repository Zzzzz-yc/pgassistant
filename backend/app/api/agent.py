"""助手接口（M4）：`/api/agent/extract`、`/api/agent/plan-draft`、`/api/agent/chat`。

契约：`docs/contracts/API.md` §3.16–3.18 与 §4（SSE 帧）、MODULES.md §4.3（异常映射）。
本文件只做四件事：**限流 → 鉴权后的入参收敛 → 分派 → 映射**。业务在 `services/`。

三条硬约束（验收逐条查）：

1. **越界请求绝不调用 LLM**：L1 命中 OUT_OF_SCOPE 时直接拿常量话术，日志可证零调用（§7.2）；
2. **客户端断连立刻断上游**：`request.is_disconnected()` 逐块检查 + `sse.stream()` 的
   `SSEDisconnected` 收尾，两处都会 `aclose` 上游并记 `aborted=true`（§7.9）；
3. **流前失败不是 SSE**：参数 / 限流 / 未配模型 → 普通 JSON 信封 + 普通状态码（§3.18）。

限流是**进程内令牌桶**（§7.10）：对话 20/min、plan-draft 3/5min、extract 10/min；
超限 429 `RATE_LIMITED` + `Retry-After`。它是防手滑，不是防攻击。
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..api.deps import get_conn
from ..core import errors
from ..db.connection import connect
from ..prompts import load_prompt, render
from ..schemas.models import (
    AgentChatRequest,
    AgentExtractRequest,
    AgentPlanDraftRequest,
    Intent,
    PlanSlots,
    SseDeltaData,
    SseDoneData,
    SseErrorData,
    SseIntentData,
    TokenUsage,
)
from ..services import plan_service, plan_templates, plan_validate, planner, search, sse
from ..services.llm_client import (
    LLMClient,
    LLMError,
    estimate_tokens,
    load_llm_config,
)
from ..services.router import (
    RouteDecision,
    capability_text,
    clarify_options,
    clarify_text,
    decide,
    greeting_text,
    normalize,
    plan_clarify_text,
    query_from_search_text,
    refusal,
)
from ..services.router.classify import classify as classify_text

log = logging.getLogger("pgassistant.agent")

router = APIRouter()

#: 进程内令牌桶：(容量, 窗口秒)。§7.10 的三个数字，逐字照抄。
LIMITS: dict[str, tuple[int, float]] = {
    "chat": (20, 60.0),
    "plan-draft": (3, 300.0),
    "extract": (10, 60.0),
}

#: 建议任务的输出预算兜底（C3：默认 4096）。实际以模型配置的 `max_tokens` 为准，
#: 只有拿不到配置（例如测试里的假 client）时才用它。
ADVICE_MAX_TOKENS = 4096

#: 检索型回答取多少张卡片（§7.3「检索 + 卡片列表」）。
SEARCH_LIMIT = 10

#: done 里最多带几段最近历史（§7.8：最近 8 条）。只用于建议提示词与日志上下文。
HISTORY_LIMIT = 8


# --------------------------------------------------------------- 限流
@dataclass
class _Bucket:
    capacity: float
    window: float
    tokens: float
    updated: float

    @property
    def rate(self) -> float:
        return self.capacity / self.window if self.window else self.capacity

    def take(self, now: float) -> float:
        """取一个令牌。拿到返回 0.0；拿不到返回需要等待的秒数。"""
        if self.updated <= 0:
            self.updated = now
        else:
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        return (1.0 - self.tokens) / self.rate if self.rate else self.window


_BUCKETS: dict[str, _Bucket] = {}


def _bucket(name: str) -> _Bucket:
    bucket = _BUCKETS.get(name)
    if bucket is None:
        capacity, window = LIMITS[name]
        bucket = _Bucket(capacity=float(capacity), window=window,
                         tokens=float(capacity), updated=0.0)
        _BUCKETS[name] = bucket
    return bucket


def reset_limits() -> None:
    """清空令牌桶（测试用；也让"重启进程即重置"这条语义可测）。"""
    _BUCKETS.clear()


def _enforce_limit(name: str) -> None:
    capacity, window = LIMITS[name]
    wait = _bucket(name).take(time.monotonic())
    if wait > 0:
        retry_after = max(1, int(math.ceil(wait)))
        raise HTTPException(
            status_code=429,
            detail=errors.error_payload(
                "RATE_LIMITED",
                "请求过于频繁",
                detail={
                    "limit": capacity,
                    "window_seconds": int(window),
                    "retry_after": retry_after,
                },
                hint="稍等几秒再试",
            ),
            headers={"Retry-After": str(retry_after)},
        )


# --------------------------------------------------------------- 小工具
def _http(status_code: int, code: str, message: str, detail=None, hint=None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=errors.error_payload(code, message, detail, hint),
    )


def _llm_http(exc: LLMError) -> HTTPException:
    """MODULES §4.3 的唯一映射：LLM 异常 → HTTP 状态码 + 错误码。"""
    status_by_code = {
        "LLM_NOT_CONFIGURED": 400,
        "LLM_401": 502,
        "LLM_429": 429,
        "LLM_TIMEOUT": 504,
        "LLM_UNAVAILABLE": 503,
        "LLM_BAD_OUTPUT": 502,
    }
    status = status_by_code.get(exc.code, 502)
    detail = None
    headers = None
    if exc.code == "LLM_429":
        retry_after = max(1, int(math.ceil(getattr(exc, "retry_after", 1.0) or 1.0)))
        detail = {"retry_after": retry_after}
        headers = {"Retry-After": str(retry_after)}
    return HTTPException(
        status_code=status,
        detail=errors.error_payload(exc.code, exc.message, detail=detail, hint=exc.hint),
        headers=headers,
    )


def _sse_error(exc: LLMError | Exception, *, code: str = "", message: str = "",
               hint: str = "", detail=None) -> SseErrorData:
    """流内错误帧：与错误信封同形，发完立即关流（§4.2 第 2 条）。"""
    if isinstance(exc, LLMError):
        detail = detail if detail is not None else getattr(exc, "detail", None)
        return SseErrorData(code=exc.code, message=exc.message, hint=exc.hint, detail=detail)
    return SseErrorData(code=code or "INTERNAL", message=message or "服务内部错误",
                        hint=hint or None, detail=detail)


def _usage(tokens_in: int = 0, tokens_out: int = 0, estimated: bool = True) -> TokenUsage:
    return TokenUsage(tokens_in=max(0, tokens_in), tokens_out=max(0, tokens_out),
                      estimated=estimated)


def _done(decision: RouteDecision | None, *, kind: str = "text",
          result=None, usage: TokenUsage | None = None,
          message_id: int | None = None, finish_reason: str = "stop",
          aborted: bool = False) -> SseDoneData:
    return SseDoneData(
        kind=kind,
        message_id=message_id,
        finish_reason=finish_reason,
        aborted=aborted,
        usage=usage or _usage(),
        intent=decision.intent if decision is not None else None,
        result=result,
    )


def _intent_event(decision: RouteDecision) -> SseIntentData:
    return SseIntentData(
        intent=decision.intent,
        confidence=decision.confidence,
        source=decision.source,
        slots=decision.slots,
        needs_clarify=decision.needs_clarify,
        clarify_options=decision.clarify_options[:3],
    )


# --------------------------------------------------------------- 历史（best-effort）
def _save_message(conn, role: str, content: str, *, intent=None, model=None,
                  tokens_in=None, tokens_out=None) -> int | None:
    """写 `chat_messages`（§7.8）。**写失败只记 warning**，不能把 200 变成 500。"""
    if conn is None or not (content or "").strip():
        return None
    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO chat_messages (role, content, intent, model, tokens_in,"
                " tokens_out, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (role, content[:4000],
                 getattr(intent, "value", intent), model, tokens_in, tokens_out,
                 time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())),
            )
        return int(cursor.lastrowid or 0) or None
    except Exception as exc:                # sqlite3.Error / 表缺失
        log.warning("event=agent.message_save_failed reason=%s", type(exc).__name__)
        return None


def _recent_messages(conn, limit: int = HISTORY_LIMIT) -> list[dict]:
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    except Exception:
        return []
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


# --------------------------------------------------------------- 检索
def _search_payload(conn, query: str) -> dict:
    """RESOURCE_SEARCH 的执行器：与 `/api/search` 同一份结构与同一份耗时口径。"""
    started = time.perf_counter()
    if conn is None:                         # 纯逻辑测试（无库）也要能跑
        result = {"items": [], "total": 0, "mode": "empty", "tokens": []}
    else:
        result = search.search(conn, query, limit=SEARCH_LIMIT)
    took_ms = max(int((time.perf_counter() - started) * 1000), 0)
    payload = {
        "query": (query or "?")[:100],
        "mode": result["mode"],
        "tokens": result["tokens"],
        "total": result["total"],
        "items": result["items"],
        "took_ms": took_ms,
        "semantic_available": None,
        "notice": None,
    }
    if conn is not None and not result["total"]:
        search.record_miss(conn, query, None)
    return payload


# --------------------------------------------------------------- 计划入参
def _plan_request(decision: RouteDecision, message: str) -> AgentPlanDraftRequest:
    slots = decision.slots
    if slots is None and decision.source == "rule":
        # L1 直接判成 PLAN_CREATE 时没有模型抽取的槽位：用 planner 的纯正则兜底，
        # 「12 周」「每周 10 小时」「零基础」「前端」这些常见说法仍然生效（零 LLM）。
        slots = planner.regex_slots(message)
    slots = slots or PlanSlots()
    goal = (slots.goal or message or "").strip()[:120] or "学习计划"
    return AgentPlanDraftRequest(
        goal=goal,
        level=slots.level or "beginner",
        weeks=slots.weeks or 12,
        weekly_hours=slots.weekly_hours or 10,
        prefer=None,
        stack=slots.stack,
        template_id=None,
    )


# --------------------------------------------------------------- extract
@router.post("/agent/extract", summary="一句话 → 表单字段（L2 抽取）")
async def agent_extract(payload: AgentExtractRequest, conn=Depends(get_conn)):
    """§3.16：`text` 1–500。未配模型 → 400 `LLM_NOT_CONFIGURED`（前端先查 health）。"""
    _enforce_limit("extract")
    text = normalize(payload.text)
    if not text:
        raise _http(422, "VALIDATION_ERROR", "消息不能为空",
                    detail={"field": "text"}, hint="输入一句话，例如「三个月转前端，每天两小时」")

    config = load_llm_config(conn)
    if config is None:
        raise _http(
            400, "LLM_NOT_CONFIGURED", "未配置模型",
            hint="到设置页填写 base_url / api_key / model；不配也能手动创建计划",
        )

    llm = LLMClient(config)
    try:
        return await planner.extract(
            conn, text, llm, locale=payload.locale, page_context=payload.page_context
        )
    except LLMError as exc:
        raise _llm_http(exc) from exc
    finally:
        await llm.aclose()


# --------------------------------------------------------------- plan-draft
@router.post("/agent/plan-draft", summary="表单 → 草稿（不落库）")
async def agent_plan_draft(payload: AgentPlanDraftRequest, conn=Depends(get_conn)):
    """§3.17：模板优先（0 token）→ 否则两段式生成。两次不过校验 → 422 `PLAN_INVALID`。"""
    _enforce_limit("plan-draft")

    if payload.template_id:
        try:
            return plan_templates.load_template(payload.template_id)
        except plan_templates.TemplateNotFound as exc:
            raise _http(404, "PLAN_TEMPLATE_NOT_FOUND", "模板不存在",
                        detail={"template_id": exc.template_id},
                        hint="不选模板、直接填表单走 AI 生成") from exc
        except plan_validate.PlanInvalid as exc:
            raise _http(422, "PLAN_INVALID", "模板未通过校验",
                        detail={"errors": exc.errors},
                        hint="换一个模板，或不选模板直接生成") from exc

    config = load_llm_config(conn)
    if config is None:
        raise _http(
            400, "LLM_NOT_CONFIGURED", "未配置模型",
            hint="到设置页填写 base_url / api_key / model；不配也能手动从零创建计划",
        )

    llm = LLMClient(config)
    try:
        return await planner.generate(conn, payload, llm)
    except plan_validate.PlanInvalid as exc:
        detail: dict = {"errors": exc.errors}
        if exc.suggestions:
            detail["suggestions"] = exc.suggestions
        raise _http(422, "PLAN_INVALID", "生成的计划未通过校验",
                    detail=detail,
                    hint="换一种描述重试，或手动从零创建计划") from exc
    except LLMError as exc:
        raise _llm_http(exc) from exc
    finally:
        await llm.aclose()


# --------------------------------------------------------------- chat（SSE）
@router.post("/agent/chat", summary="SSE 流式对话")
async def agent_chat(request: Request, payload: AgentChatRequest, conn=Depends(get_conn)):
    """§3.18：流前失败用普通信封；流内错误用 `event: error`。"""
    _enforce_limit("chat")

    message = normalize(payload.message)
    if not message:
        raise _http(422, "VALIDATION_ERROR", "消息不能为空",
                    detail={"field": "message"},
                    hint="输入一句话，例如「有没有讲闭包的文章」")

    decision = decide(message, locale=payload.locale)
    config = load_llm_config(conn)
    # L1 的计划意图但信息不足（「我想学前端」）用常量话术追问，零 LLM（C2）；
    # fallback（需要 L2）与真正要生成/调整/建议的意图才要求模型可用。
    needs_llm = decision.source == "fallback" or (
        decision.intent in (Intent.PLAN_CREATE, Intent.PLAN_ADJUST, Intent.ADVICE)
        and not decision.needs_clarify
    )
    if needs_llm and config is None:
        raise _http(
            400, "LLM_NOT_CONFIGURED", "未配置模型",
            hint="到设置页配置；浏览、搜索与手动创建计划不受影响",
        )

    llm = LLMClient(config)
    events = _chat_events(request, payload, message, decision, llm)
    return StreamingResponse(
        _guarded(events),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-store",
            "x-accel-buffering": "no",
            "connection": "keep-alive",
        },
    )


async def _guarded(events):
    """把断连翻译成一条日志。`sse.stream` 负责关上游，这里只负责记 `aborted`。"""
    started = time.perf_counter()
    try:
        async for chunk in sse.stream(events):
            yield chunk
    except sse.SSEDisconnected:
        log.info("event=agent.chat aborted=true duration_ms=%d",
                 int((time.perf_counter() - started) * 1000))
        raise
    else:
        log.info("event=agent.chat aborted=false duration_ms=%d",
                 int((time.perf_counter() - started) * 1000))


async def _chat_events(request: Request, payload: AgentChatRequest, message: str,
                       decision: RouteDecision, llm: LLMClient):
    """一次对话的完整事件流：intent → (delta)* → (done|error)。"""
    started = time.perf_counter()
    conn = None
    decision_local = decision
    try:
        # L1 没命中 → L2。这里失败要按 §4.2 第 1 条先补一个 intent 帧，再发 error。
        if decision_local.source == "fallback":
            try:
                decision_local = await classify_text(message, llm, locale=payload.locale)
            except LLMError as exc:
                yield _intent_event(decision_local)
                yield _sse_error(exc)
                return
        yield _intent_event(decision_local)

        conn = connect(_db_path())
        _save_message(conn, "user", message)

        intent = decision_local.intent
        if intent == Intent.OUT_OF_SCOPE:
            # 常量话术：L1 已拒绝，模型一次都没被调用（§7.7）
            yield SseDeltaData(text=refusal(payload.locale))
            message_id = _save_message(conn, "assistant", refusal(payload.locale),
                                       intent=intent)
            yield _done(decision_local, kind="text", message_id=message_id)
        elif intent == Intent.GREETING:
            yield SseDeltaData(text=greeting_text(payload.locale))
            # 文本型回复落库（D26 §26.6）：落的就是实际发出去的常量话术
            message_id = _save_message(conn, "assistant", greeting_text(payload.locale),
                                       intent=intent)
            yield _done(decision_local, kind="text", message_id=message_id)
        elif intent == Intent.CAPABILITY:
            yield SseDeltaData(text=capability_text(payload.locale))
            message_id = _save_message(conn, "assistant", capability_text(payload.locale),
                                       intent=intent)
            yield _done(decision_local, kind="text", message_id=message_id)
        elif intent == Intent.CLARIFY:
            # 澄清卡片：选项在 intent.clarify_options 里（§4.3）。注入检测命中时也走这里，
            # 那段输入绝不会送进模型。落库的是正文（澄清文案），不是选项列表。
            yield SseDeltaData(text=clarify_text(payload.locale))
            message_id = _save_message(conn, "assistant", clarify_text(payload.locale),
                                       intent=intent)
            yield _done(decision_local, kind="clarify", message_id=message_id)
        elif intent == Intent.RESOURCE_SEARCH:
            query = query_from_search_text(message)
            result = _search_payload(conn, query)
            yield _done(decision_local, kind="search", result=result)
        elif intent == Intent.PLAN_CREATE:
            if decision_local.needs_clarify:
                # 信息不足（只说「我想学前端」）：方向性提问，常量话术，零 LLM（C2）。
                yield SseDeltaData(text=plan_clarify_text(payload.locale))
                message_id = _save_message(conn, "assistant",
                                           plan_clarify_text(payload.locale),
                                           intent=decision_local.intent)
                yield _done(decision_local, kind="clarify", message_id=message_id)
            else:
                async for event in _plan_events(conn, _plan_request(decision_local, message),
                                                decision_local, llm):
                    yield event
        elif intent == Intent.PLAN_ADJUST:
            async for event in _adjust_events(conn, payload, decision_local, message, llm):
                yield event
        else:                                # ADVICE
            async for event in _advice_events(request, conn, decision_local, message,
                                              payload, llm):
                yield event
    except sse.SSEDisconnected:
        raise
    except LLMError as exc:
        yield _sse_error(exc)
    except Exception as exc:                 # 未捕获：给前端一个 error 帧，不吐栈
        log.exception("event=agent.chat internal_error type=%s", type(exc).__name__)
        yield _sse_error(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        await llm.aclose()
        log.info("event=agent.chat.finish intent=%s duration_ms=%d",
                 getattr(decision_local.intent, "value", decision_local.intent),
                 int((time.perf_counter() - started) * 1000))


def _db_path():
    from ..api import deps

    return deps.DB_PATH


async def _plan_events(conn, req: AgentPlanDraftRequest, decision: RouteDecision,
                       llm: LLMClient):
    try:
        draft = await planner.generate(conn, req, llm)
    except plan_validate.PlanInvalid as exc:
        detail: dict = {"errors": exc.errors}
        if exc.suggestions:
            detail["suggestions"] = exc.suggestions
        yield SseErrorData(
            code="PLAN_INVALID",
            message="生成的计划未通过校验",
            detail=detail,
            hint="换一种描述重试，或手动从零创建计划",
        )
        return
    yield _done(decision, kind="plan_draft", result=draft)


async def _adjust_events(conn, payload: AgentChatRequest, decision: RouteDecision,
                         message: str, llm: LLMClient):
    plan_id = payload.page_context.plan_id if payload.page_context else None
    if not plan_id:
        yield SseDeltaData(text=clarify_text(payload.locale))
        yield _done(decision, kind="clarify")
        return
    try:
        plan = plan_service.get_plan(conn, plan_id)
        draft = await planner.adjust(conn, plan, message, llm)
    except plan_service.PlanNotFound:
        yield SseErrorData(
            code="PLAN_NOT_FOUND", message="计划不存在或已删除",
            detail={"plan_id": plan_id}, hint="刷新计划列表后重试",
        )
        return
    except plan_validate.PlanInvalid as exc:
        detail: dict = {"errors": exc.errors}
        if exc.suggestions:
            detail["suggestions"] = exc.suggestions
        yield SseErrorData(
            code="PLAN_INVALID", message="生成的调整草稿未通过校验",
            detail=detail, hint="换一种说法重试，或手动编辑计划",
        )
        return
    yield _done(decision, kind="plan_draft", result=draft)


async def _advice_events(request: Request, conn, decision: RouteDecision, message: str,
                         payload: AgentChatRequest, llm: LLMClient):
    """ADVICE：必须引用站内资料 + 免责声明（§7.3），走流式（§7.8）。"""
    resources = _search_payload(conn, query_from_search_text(message))["items"][:5]
    page_context = payload.page_context.model_dump(exclude_none=True) if payload.page_context else {}
    history = _recent_messages(conn)[-4:]
    prompt = render(
        load_prompt("advice"),
        QUESTION=message,
        LOCALE=payload.locale,
        PAGE_CONTEXT=json.dumps(page_context, ensure_ascii=False) if page_context else "空",
        RESOURCES=json.dumps(resources, ensure_ascii=False, indent=1) if resources else "[]",
        HISTORY="\n".join(
            "%s: %s" % (item["role"], item["content"][:200]) for item in history
        ) or "无",
    )
    messages = [{"role": "system", "content": prompt}]

    chunks: list[str] = []
    aborted = False
    # C3：跟随设置里的 `llm.max_tokens`（默认 4096）；拿不到配置时退回常量。
    configured = getattr(getattr(llm, "config", None), "max_tokens", None)
    max_tokens = configured if isinstance(configured, int) and configured > 0 \
        else ADVICE_MAX_TOKENS
    agen = llm.stream(messages, max_tokens=max_tokens)
    try:
        async for chunk in agen:
            if not chunk:
                continue
            if await request.is_disconnected():
                # 立刻收上游（§7.9：不断等于白烧钱）
                aborted = True
                break
            chunks.append(chunk)
            yield SseDeltaData(text=chunk)
    finally:
        await agen.aclose()

    text = "".join(chunks)
    if not aborted and text:
        _save_message(conn, "assistant", text, intent=decision.intent)
    yield _done(
        decision,
        kind="text",
        usage=_usage(estimate_tokens(prompt), estimate_tokens(text) if text else 0),
        finish_reason="aborted" if aborted else "stop",
        aborted=aborted,
    )
