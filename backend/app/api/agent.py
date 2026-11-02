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

import hashlib
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
    last_l1_rule,
    normalize,
    plan_clarify_text,
    query_from_search_text,
    refusal,
    should_escalate,
)
from ..services.router.classify import classify as classify_text, extract_slots_llm
from ..services.router.slots import (
    DIRECTION_LABEL,
    SlotResult,
    extract_slots,
    merge_slots,
)

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


# --------------------------------------------------------------- L1.5 升级
#: L2 推翻 L1 的置信度门槛：够高才换 intent，不够只补槽位（保 L1 的意图）。
ESCALATION_MIN_CONFIDENCE = 0.6


def _slot_result_from_plan(values: PlanSlots | None) -> SlotResult:
    """契约槽位（`PlanSlots`）→ 内部 `SlotResult`（`merge_slots` 的入参）。

    `PlanSlots` 没有 `topic`（检索主题词）这一栏，这里留空：合并只补方向/周数等字段，
    检索主题词始终以 L1 的规则抽取为准（`_search_slots` 会重新抽一次）。
    """
    if values is None:
        return SlotResult()
    return SlotResult(
        stack=values.stack,
        level=values.level.value if hasattr(values.level, "value") else values.level,
        goal=values.goal,
        weeks=values.weeks,
        weekly_hours=values.weekly_hours,
        complete=bool(values.stack or values.goal),
    )


def _plan_slots_from_result(found: SlotResult) -> PlanSlots | None:
    """`SlotResult` → 契约槽位；空的返回 None（别给 intent 帧塞一个空对象）。

    `weeks` / `weekly_hours` 按 `PlanSlots` 的边界夹取（越界值夹到边界，不 422）。
    """
    values: dict = {}
    if found.goal:
        values["goal"] = found.goal[:120]
    if found.level:
        values["level"] = found.level
    if found.stack:
        values["stack"] = found.stack
    if found.weeks is not None:
        values["weeks"] = min(52, max(4, found.weeks))
    if found.weekly_hours is not None:
        values["weekly_hours"] = min(40, max(1, found.weekly_hours))
    return PlanSlots(**values) if values else None


def merge_escalation(decision: RouteDecision, llm_decision: RouteDecision,
                     message: str, locale: str = "zh-CN") -> RouteDecision:
    """把 L2 的分类结果并回 L1 的弱命中（纯函数，便于单测）。

    - L2 成功且 `confidence >= ESCALATION_MIN_CONFIDENCE` → **以 L2 的 intent 为准**；
    - L2 成功但置信度不足 → **保留 L1 的 intent**，只合并槽位；
    - 槽位一律 `slots.merge_slots(规则槽位, L2 槽位)`：规则优先，模型只填空位。
    - L2 自己降级成 `source="fallback"`（两次输出都不合法）不算成功，按低置信处理。
    """
    base = (_slot_result_from_plan(decision.slots) if decision.slots is not None
            else extract_slots(message, locale=locale))
    merged = merge_slots(base, _slot_result_from_plan(llm_decision.slots))
    slots_out = _plan_slots_from_result(merged)

    trusted = llm_decision.source == "llm" and \
        llm_decision.confidence >= ESCALATION_MIN_CONFIDENCE
    if trusted:
        return RouteDecision(
            intent=llm_decision.intent,
            confidence=llm_decision.confidence,
            source="llm",
            slots=slots_out,
            needs_clarify=llm_decision.needs_clarify,
            clarify_options=list(llm_decision.clarify_options),
        )
    return RouteDecision(
        intent=decision.intent,
        confidence=decision.confidence,
        source=decision.source,
        slots=slots_out if slots_out is not None else decision.slots,
        needs_clarify=decision.needs_clarify,
        clarify_options=list(decision.clarify_options),
    )


# --------------------------------------------------------------- 路由可观测性
#: `text_head` 的截断长度。日志在本机，但仍按最小化原则只留开头，不落完整原文。
TEXT_HEAD_LIMIT = 80


def _text_fingerprint(text: str) -> dict:
    """`text_len` / `text_head`（≤80 字）/ `text_sha1`（前 8 位）——方便按原文反查同一条。"""
    body = text or ""
    return {
        "text_len": len(body),
        "text_head": body[:TEXT_HEAD_LIMIT],
        "text_sha1": hashlib.sha1(body.encode("utf-8")).hexdigest()[:8],
    }


def _log_decision(decision: RouteDecision, text: str, *, rule: str | None,
                  escalated: bool = False, escalation_reason: str = "") -> None:
    """记一次**最终**路由决策（一次请求一条）；只写日志，不参与任何判定。

    `rule` 只有 L1 命中时非空；走 L2 的样本留 null，好让 `scripts/router_stats.py`
    把「哪条规则吃掉的」和「模型判的」分开统计。

    `escalated` / `escalation_reason` 是**新增**字段（原有字段一个不动）：前者表示
    这一条有没有真的问过 L2，后者是 `should_escalate()` 给出的短标签，方便统计
    「哪些弱命中被升级」「哪些因为槽位够用而没升级」。
    """
    payload = {
        "event": "router_decision",
        "source": decision.source,
        "intent": getattr(decision.intent, "value", decision.intent),
        "confidence": decision.confidence,
        "rule": rule,
        "escalated": bool(escalated),
        "escalation_reason": escalation_reason,
    }
    payload.update(_text_fingerprint(text))
    log.info(json.dumps(payload, ensure_ascii=False))


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
async def _search_slots(message: str, locale: str, llm: LLMClient) -> SlotResult:
    """`RESOURCE_SEARCH` 的目标槽位：规则先抽，抽不全**且**有模型时才补抽一次。

    降级是产品底线：没配模型 / 模型出错都只用规则槽位，检索照常返回，不报错、不 500。
    """
    found = extract_slots(message, locale=locale)
    if found.complete:
        return found
    if not llm.configured:
        # 没模型：只用规则槽位，检索照常返回（产品底线），但要留一条降级日志
        log.info("event=agent.search_slots fallback=rule reason=no_model")
        return found
    extra = await extract_slots_llm(message, llm, locale=locale)
    if extra is None:
        log.info("event=agent.search_slots fallback=rule reason=llm_failed")
        return found
    merged = merge_slots(found, extra)
    log.info("event=agent.search_slots source=llm complete=%s stack=%s level=%s",
             merged.complete, merged.stack, merged.level)
    return merged


def _search_query(message: str, found: SlotResult) -> str:
    """检索词：有主题词就只用主题词；**只有方向**时才把方向词当查询词。

    不把 `stack` 叠加到主题词上（「闭包 前端」这种 AND 组合正是空结果的来源），
    `stack` 由 `search.search(stack=...)` 单独承载。返回空串时退回（截断的）原句。
    """
    if found.topic:
        return found.topic
    cleaned = query_from_search_text(message)
    if cleaned and cleaned != normalize(message):
        return cleaned
    return DIRECTION_LABEL.get(found.stack or "") or cleaned or message[:100]


def _search_payload(conn, query: str, *, stack: str | None = None,
                    level: str | None = None) -> dict:
    """RESOURCE_SEARCH 的执行器：与 `/api/search` 同一份结构与同一份耗时口径。

    槽位过滤有两级降级（都不改契约）：

    1. `stack` + `level` 一起过滤；
    2. 命中 0 条 → 丢掉 `level` 再试一次：`level` 是"期望难度"不是硬条件，否则
       「冲击前端岗位」会因为站内还没有 advanced 前端资料而一条都不给；
    3. 查询词被完全清空（只有方向、没有主题词，`mode == "empty"`）→ 按 `stack`
       浏览（`search.browse`）。真·缺内容（`mode == "miss"`）不走这一步，照常记 miss。
    """
    started = time.perf_counter()
    relaxed = ""
    if conn is None:                         # 纯逻辑测试（无库）也要能跑
        result = {"items": [], "total": 0, "mode": "empty", "tokens": []}
    else:
        result = search.search(conn, query, stack=stack, level=level, limit=SEARCH_LIMIT)
        if result["total"] == 0 and level is not None:
            result = search.search(conn, query, stack=stack, limit=SEARCH_LIMIT)
            if result["total"]:
                relaxed = "level"
        if result["total"] == 0 and stack is not None and result["mode"] == "empty":
            result = search.browse(conn, stack=stack, limit=SEARCH_LIMIT)
            if result["total"]:
                relaxed = (relaxed + "+browse") if relaxed else "browse"
        if relaxed:
            log.info("event=agent.search_filters relaxed=%s stack=%s", relaxed, stack)
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
        filters = {key: value for key, value in (("stack", stack), ("level", level))
                   if value}
        search.record_miss(conn, query, filters or None)
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
    # L1 弱命中（search / plan_create）**且**有模型时再问一次 L2：规则命中 ≠ 判对。
    # 无模型时 `should_escalate` 永远返回 False，行为与升级前完全一致（产品底线）。
    escalate, escalation_reason = should_escalate(
        decision, message, payload.locale, llm_available=config is not None)
    escalated = False
    if escalate:
        try:
            llm_decision = await classify_text(message, llm, locale=payload.locale)
        except LLMError as exc:
            # 升级失败不是用户的问题：保留 L1，只记一条 warn，绝不发 error 帧。
            log.warning("event=agent.escalation result=l1 code=%s reason=%s",
                        exc.code, escalation_reason)
        else:
            decision = merge_escalation(decision, llm_decision, message, payload.locale)
            escalated = True
            log.info("event=agent.escalation result=l2 intent=%s confidence=%.2f reason=%s",
                     decision.intent.value, decision.confidence, escalation_reason)
    # 一次请求一条最终决策：L1 命中（含升级后）在这里记，fallback 的那条在 `_chat_events`。
    if decision.source != "fallback":
        _log_decision(decision, message, rule=last_l1_rule(),
                      escalated=escalated, escalation_reason=escalation_reason)

    events = _chat_events(request, payload, message, decision, llm,
                          escalation_reason=escalation_reason)
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
                       decision: RouteDecision, llm: LLMClient,
                       escalation_reason: str = "not_rule"):
    """一次对话的完整事件流：intent → (delta)* → (done|error)。

    `escalation_reason` 只用于日志：fallback 这条路不走 L1.5 升级（本来就要进 L2），
    记 false + 调用方给的标签即可（`router_decision` 的字段一个不少）。
    """
    started = time.perf_counter()
    conn = None
    decision_local = decision
    try:
        # L1 没命中 → L2。这里失败要按 §4.2 第 1 条先补一个 intent 帧，再发 error。
        if decision_local.source == "fallback":
            try:
                decision_local = await classify_text(message, llm, locale=payload.locale)
            except LLMError as exc:
                # L2 故障：决策停在 L1 的 fallback 上（source=fallback），error 帧前也留一条日志。
                _log_decision(decision_local, message, rule=None,
                              escalation_reason=escalation_reason)
                yield _intent_event(decision_local)
                yield _sse_error(exc)
                return
            # 最终决策来源是 llm（含低置信度降级成 CLARIFY 的情况）。
            _log_decision(decision_local, message, rule=None,
                          escalation_reason=escalation_reason)
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
            # 槽位：规则先抽（零网络），抽不全且有模型时补抽一次（见 `_search_slots`）。
            found = await _search_slots(message, payload.locale, llm)
            query = _search_query(message, found)
            result = _search_payload(conn, query, stack=found.stack, level=found.level)
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
        # 没有 plan_id 时发的是 CLARIFY 文本回复（按形态归入 D26 §26.6 第 2 条），要落库
        yield SseDeltaData(text=clarify_text(payload.locale))
        message_id = _save_message(conn, "assistant", clarify_text(payload.locale),
                                   intent=decision.intent)
        yield _done(decision, kind="clarify", message_id=message_id)
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
