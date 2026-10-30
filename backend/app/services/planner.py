"""计划生成（M4，§7.6 两段式 / §8.6 模板）。

三段，缺一不可：

1. **第一段 检索候选**（`candidates()`，不经过 LLM）：按表单的 stack/level/goal 检索，
   压成 `slug/title/level/type/duration_min/summary`，最多 60 条；
2. **第二段 让模型在候选集内编排**：提示词明确"只能使用候选集中的 slug"，
   缺失主题允许 `resource_slug=null` 并标"自定义任务"；
3. **第三段 服务端校验**（`plan_validate.validate_draft`）：白名单外的条目**丢弃**、
   条目数/总时长/难度倒挂不过 → 带着错误回填重试 1 次；两次都失败抛
   `PlanInvalid(suggestions=top10)` → 422 带兜底素材（§7.6）。
   **任何情况下都不把未通过校验的 JSON 交给前端。**

模板优先（`template_id` 非空）：`plan_templates.load_template`，0 token。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..prompts import load_prompt, render
from ..schemas.models import (
    AgentExtractResponse,
    AgentPlanDraftRequest,
    ItemDraft,
    PageContext,
    PlanDraft,
    PlanRead,
    PlanSlots,
    StageDraft,
)
from . import plan_templates, plan_validate, search
from .llm_client import (
    LLMClient,
    LLMError,
    LLMFormatUnsupported,
    strip_fences,
)
from .router import clarify_options, normalize
from .router.classify import classify

log = logging.getLogger("pgassistant.planner")

#: 候选集上限（§7.6：top 60）。
CANDIDATE_LIMIT = 60

#: 生成失败重试次数：首次 + 1 次回填重试（§7.6 第三段）。
MAX_ATTEMPTS = 2

#: 计划生成的输出预算（§7.10：计划 4096）。
PLAN_MAX_TOKENS = 4096

#: 兜底建议条数（§7.6：该目标的 top 10 资料）。
SUGGESTION_LIMIT = 10

#: 模型输出的键集合收敛白名单（见 `_coerce_draft_payload`）。
_DRAFT_FIELDS = ("title", "goal", "weeks", "weekly_hours", "stages")
_STAGE_FIELDS = ("title", "weeks", "description", "items")
_ITEM_FIELDS = ("title", "resource_slug", "est_minutes", "note")


@dataclass(frozen=True)
class Candidate:
    """第一段的产物（与 §7.6 候选集 JSON 同构）。"""

    slug: str
    title: str
    level: str
    type: str
    duration_min: int | None
    summary: str


#: 技术栈内候选少于这个数就放宽栈过滤（小内容包的兜底，见 `candidates()`）。
_RELAX_THRESHOLD = 10


def _fallback_rows(conn, stack: str | None, level: str | None, limit: int):
    """按 stack/level 直接取候选（补足 FTS 召回不足的那部分）。"""
    sql = [
        "SELECT slug, title, level, type, duration_min, summary FROM resources",
        "WHERE is_deleted = 0 AND type <> 'overview'",
    ]
    params: list = []
    if stack:
        sql.append("AND stack = ?")
        params.append(stack)
    sql.append(
        "ORDER BY CASE WHEN level = ? THEN 0 ELSE 1 END,"
        " CASE level WHEN 'beginner' THEN 0 WHEN 'intermediate' THEN 1 ELSE 2 END,"
        " COALESCE(duration_min, 30), slug LIMIT ?"
    )
    params.extend([level or "beginner", max(0, limit)])
    try:
        return conn.execute(" ".join(sql), params).fetchall()
    except Exception as exc:            # 表缺失 / 只读库：候选集为空但不 500
        log.warning("event=planner.candidates sql_failed=%s", type(exc).__name__)
        return []


# --------------------------------------------------------------- 第一段
def candidates(conn, req: AgentPlanDraftRequest, *, limit: int = CANDIDATE_LIMIT
               ) -> list[Candidate]:
    """检索候选：先按 goal 走 FTS（复用 `services/search.py`），再用 stack/level 补足。

    为什么要补：目标文本常常只有"转前端"这种没有正文命中词的短句，单靠 FTS 会只剩
    两三条候选，模型就没东西可编排了。补足阶段只读 `resources` 表，同样不经过 LLM。
    """
    stack = (req.stack or "").strip() or None
    level = getattr(req.level, "value", req.level) or None
    goal = (req.goal or "").strip()
    seen: dict[str, Candidate] = {}

    def push(slug: str, title: str, item_level: str, rtype: str,
             duration, summary) -> None:
        slug = (slug or "").strip()
        if not slug or slug in seen:
            return
        seen[slug] = Candidate(
            slug=slug,
            title=(title or "").strip() or slug,
            level=(item_level or "beginner"),
            type=(rtype or "article"),
            duration_min=int(duration) if duration is not None else None,
            summary=(summary or "").strip(),
        )

    if goal:
        try:
            result = search.search(conn, goal, stack=stack, limit=limit)
        except Exception as exc:            # FTS 语法/缺表：候选集退回 SQL，不让生成 500
            log.warning("event=planner.candidates fts_failed=%s", type(exc).__name__)
            result = {"items": []}
        for item in result.get("items", []):
            push(item.get("slug"), item.get("title"), item.get("level"),
                 item.get("type"), item.get("duration_min"), item.get("summary"))

    remaining = limit - len(seen)
    if remaining > 0:
        rows = _fallback_rows(conn, stack, level, remaining)
        # 技术栈过滤太狠（小内容包 / 目标栈还没内容）时放宽一次：
        # 宁可给模型一份混杂候选，也不要给空候选集让它只能编"自定义任务"。
        if stack and len(rows) < _RELAX_THRESHOLD:
            rows = _fallback_rows(conn, None, level, remaining)
        for row in rows:
            push(row["slug"], row["title"], row["level"], row["type"],
                 row["duration_min"], row["summary"])

    items = list(seen.values())
    prefer = getattr(req, "prefer", None)
    if prefer in ("video", "docs"):
        # 稳定排序：组内保持检索相关度顺序（§7.6 的 prefer 只是偏好）
        items.sort(key=lambda candidate: 0 if candidate.type == prefer else 1)
    return items[:limit]


# --------------------------------------------------------------- 第二段
def _candidate_payload(items: list[Candidate]) -> list[dict]:
    return [
        {
            "slug": item.slug,
            "title": item.title,
            "level": item.level,
            "type": item.type,
            "duration_min": item.duration_min,
            "summary": item.summary[:200],
        }
        for item in items
    ]


def _plan_messages(req: AgentPlanDraftRequest, items: list[Candidate], *,
                   feedback: str = "", instruction: str = "",
                   base_plan: str = "") -> list[dict]:
    prompt = render(
        load_prompt("plan"),
        CANDIDATES=json.dumps(_candidate_payload(items), ensure_ascii=False, indent=1),
        GOAL=req.goal,
        LEVEL=getattr(req.level, "value", req.level),
        WEEKS=req.weeks,
        WEEKLY_HOURS=req.weekly_hours,
        TARGET_MINUTES=req.weeks * req.weekly_hours * 60,
        INSTRUCTION=instruction or "无（从零生成一份新计划）",
        BASE_PLAN=base_plan or "无",
        FEEDBACK=feedback or "无",
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "请按上面的要求输出 plan_draft JSON。"},
    ]


async def _ask_plan(llm: LLMClient, messages: list[dict]) -> str:
    try:
        response = await llm.chat(
            messages,
            max_tokens=PLAN_MAX_TOKENS,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except LLMFormatUnsupported:
        log.info("event=planner.generate fallback=prompt_only reason=response_format")
        response = await llm.chat(messages, max_tokens=PLAN_MAX_TOKENS, temperature=0.2)
    return response.text


def _to_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group())
    return None


def _coerce_draft_payload(data: dict) -> dict:
    """把模型输出的键集合收敛成 `PlanDraft` 认的字段。

    收敛只做**取键与类型转换**，取值仍由同一个 Pydantic 模型校验（模板路径不做收敛，
    `extra="forbid"` 留给人工编辑的模板去暴露拼写错误）。
    """
    if not isinstance(data, dict):
        raise ValueError("plan_draft 顶层必须是 JSON 对象")
    payload: dict = {key: data[key] for key in _DRAFT_FIELDS if key in data}
    for key in ("weeks", "weekly_hours"):
        if key in payload:
            converted = _to_int(payload[key])
            if converted is None:
                payload.pop(key)
            else:
                payload[key] = converted

    stages = payload.get("stages")
    if not isinstance(stages, list):
        raise ValueError("plan_draft.stages 必须是数组")
    cleaned_stages = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        item_payload = {key: stage[key] for key in _STAGE_FIELDS if key in stage}
        if "weeks" in item_payload:
            converted = _to_int(item_payload["weeks"])
            item_payload["weeks"] = converted if converted is not None else 1
        raw_items = stage.get("items")
        rows = raw_items if isinstance(raw_items, list) else []
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry = {key: row[key] for key in _ITEM_FIELDS if key in row}
            slug = entry.get("resource_slug")
            if slug is not None:
                entry["resource_slug"] = str(slug).strip() or None
            if "est_minutes" in entry:
                converted = _to_int(entry["est_minutes"])
                if converted is None:
                    entry.pop("est_minutes")     # 交给 plan_validate 按 duration_min/30 填充
                else:
                    entry["est_minutes"] = converted
            if "note" in entry and entry["note"] is None:
                entry["note"] = ""
            items.append(entry)
        item_payload["items"] = items
        cleaned_stages.append(item_payload)
    payload["stages"] = cleaned_stages
    return payload


def _parse_draft(text: str) -> PlanDraft:
    body = strip_fences(text)
    if not body:
        raise ValueError("空输出")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError("不是合法 JSON: %s" % exc) from exc
    return PlanDraft(**_coerce_draft_payload(data))


def _suggestions(items: list[Candidate]) -> list[dict]:
    return [
        {"slug": item.slug, "title": item.title, "duration_min": item.duration_min}
        for item in items[:SUGGESTION_LIMIT]
    ]


def _feedback(errors: list[str]) -> str:
    head = "；".join(errors[:5])
    return (
        "上一次草稿未通过服务端校验：" + head + "。\n"
        "请修正后**重新输出完整** plan_draft JSON；仍只能用候选集里的 slug。"
    )


# --------------------------------------------------------------- 生成
async def generate(conn, req: AgentPlanDraftRequest, llm: LLMClient) -> PlanDraft:
    """模板优先；否则两段式生成 + 校验。两次都不过 → `PlanInvalid(suggestions=top10)`。"""
    template_id = getattr(req, "template_id", None)
    if template_id:
        # 0 token：模板本身就是校验过的 plan_draft（§8.6「套用模板」）
        return plan_templates.load_template(template_id)

    items = candidates(conn, req)
    allowed = {item.slug for item in items}
    level_by_slug = {item.slug: item.level for item in items}
    duration_by_slug = {
        item.slug: item.duration_min for item in items if item.duration_min
    }
    target_minutes = req.weeks * req.weekly_hours * 60

    errors: list[str] = []
    feedback = ""
    for attempt in range(MAX_ATTEMPTS):
        messages = _plan_messages(req, items, feedback=feedback)
        raw = await _ask_plan(llm, messages)
        try:
            draft = _parse_draft(raw)
            plan_validate.fill_estimates(draft, duration_by_slug)
            cleaned, warnings = plan_validate.validate_draft(
                draft,
                allowed_slugs=allowed,
                level_by_slug=level_by_slug,
                target_minutes=target_minutes,
            )
            if warnings:
                log.info("event=planner.warnings count=%d", len(warnings))
            return cleaned
        except plan_validate.PlanInvalid as exc:
            errors = exc.errors
        except ValueError as exc:            # JSON 解析失败 / 结构校验失败
            errors = [str(exc)]
        log.info("event=planner.reject attempt=%d errors=%d", attempt + 1, len(errors))
        feedback = _feedback(errors)

    log.warning("event=planner.failed attempts=%d", MAX_ATTEMPTS)
    raise plan_validate.PlanInvalid(errors, suggestions=_suggestions(items))


# --------------------------------------------------------------- 调整
def plan_to_draft(plan: PlanRead) -> PlanDraft:
    """`PlanRead` → `PlanDraft`（调整路径的输入基线）。"""
    try:
        return PlanDraft(
            title=plan.title,
            goal=plan.goal or plan.title,
            weeks=plan.horizon_weeks,
            weekly_hours=plan.weekly_hours,
            stages=[
                StageDraft(
                    title=stage.title,
                    weeks=max(1, int(stage.weeks or 1)),
                    description=stage.description or "",
                    items=[
                        ItemDraft(
                            title=item.title,
                            resource_slug=item.resource_slug,
                            est_minutes=item.est_minutes,
                            note=item.note or "",
                        )
                        for item in stage.items
                    ] or [ItemDraft(title=stage.title, est_minutes=30)],
                )
                for stage in plan.stages
            ],
        )
    except ValueError as exc:
        raise plan_validate.PlanInvalid(["当前计划的结构无法转换成草稿：%s" % exc]) from exc


async def adjust(conn, plan: PlanRead, instruction: str, llm: LLMClient) -> PlanDraft:
    """PLAN_ADJUST：只允许重排 / 替换条目，**不推翻模板结构**（§8.6）。

    实现方式：一次生成后强制回填原有阶段标题、周数与阶段数；条目仍在原阶段内替换。
    阶段数变了、或两次都不过校验 → `PlanInvalid`。
    """
    base = plan_to_draft(plan)
    req = AgentPlanDraftRequest(
        goal=base.goal,
        level=plan.start_level,
        weeks=base.weeks,
        weekly_hours=base.weekly_hours,
        stack=None,
    )
    items = candidates(conn, req)
    existing = {
        item.resource_slug
        for stage in base.stages
        for item in stage.items
        if item.resource_slug
    }
    allowed = {item.slug for item in items} | existing
    level_by_slug = {item.slug: item.level for item in items}
    target_minutes = base.weeks * base.weekly_hours * 60
    instruction = normalize(instruction, max_len=500)
    base_plan_json = base.model_dump_json()

    errors: list[str] = []
    feedback = ""
    for attempt in range(MAX_ATTEMPTS):
        messages = _plan_messages(
            req, items, feedback=feedback, instruction=instruction,
            base_plan=base_plan_json,
        )
        raw = await _ask_plan(llm, messages)
        try:
            generated = _parse_draft(raw)
            if len(generated.stages) != len(base.stages):
                raise plan_validate.PlanInvalid(
                    [
                        "调整后的阶段数 %d 与原计划阶段数 %d 不一致：只允许重排/替换条目，"
                        "不得新增或删除阶段" % (len(generated.stages), len(base.stages))
                    ]
                )
            merged: list[StageDraft] = []
            for original, stage in zip(base.stages, generated.stages):
                kept = stage.items[: max(1, len(original.items))]
                if not kept:
                    kept = list(original.items)
                merged.append(
                    original.model_copy(update={"items": kept})
                )
            draft = base.model_copy(
                update={
                    "stages": merged,
                    "goal": generated.goal or base.goal,
                    "title": generated.title or base.title,
                }
            )
            cleaned, warnings = plan_validate.validate_draft(
                draft,
                allowed_slugs=allowed,
                level_by_slug=level_by_slug,
                target_minutes=target_minutes,
            )
            if warnings:
                log.info("event=planner.adjust warnings=%d", len(warnings))
            return cleaned
        except plan_validate.PlanInvalid as exc:
            errors = exc.errors
        except ValueError as exc:
            errors = [str(exc)]
        log.info("event=planner.adjust_reject attempt=%d errors=%d", attempt + 1, len(errors))
        feedback = _feedback(errors)

    raise plan_validate.PlanInvalid(errors, suggestions=_suggestions(items))


# --------------------------------------------------------------- 抽取
_LEVEL_WORDS = (
    ("advanced", ("高级", "资深", "专家", "老手", "advanced")),
    ("intermediate", ("有基础", "有经验", "进阶", "中级", "intermediate")),
    ("beginner", ("零基础", "入门", "新手", "刚开始", "小白", "beginner")),
)

_STACK_WORDS = (
    ("frontend", ("前端", "react", "vue", "javascript", "css", "html")),
    ("backend", ("后端", "服务端", "数据库", "http", "redis", "nginx", "docker")),
    ("cs-basics", ("算法", "数据结构", "复杂度", "动态规划", "二叉树", "排序")),
    ("languages", ("python", "java", "语言基础", "编程语言")),
)

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_NUM = r"([0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十]+)"
_MONTHS_RE = re.compile(_NUM + r"\s*个?\s*月")
_WEEKS_RE = re.compile(_NUM + r"\s*(?:个)?\s*(?:周|星期)")
_DAILY_HOURS_RE = re.compile(r"每(?:天|日)\s*" + _NUM + r"\s*(?:个)?\s*小时")
_WEEKLY_HOURS_RE = re.compile(r"每\s*(?:周|星期|礼拜)\s*" + _NUM + r"\s*(?:个)?\s*小时")
_HALF_YEAR_RE = re.compile(r"半年")
_ONE_YEAR_RE = re.compile(r"(?:一|1)\s*年")


def _cn_number(raw: str) -> float | None:
    """中文/阿拉伯数字 → float。只支持 ≤99 的常用写法（周数/小时数够用）。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    if "十" in raw:
        head, _, tail = raw.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return float(tens * 10 + ones)
    if raw in _CN_DIGITS:
        return float(_CN_DIGITS[raw])
    return None


def regex_slots(text: str) -> PlanSlots:
    """正则兜底抽取（§7.9「LLM 不可用时也不能报错」）。

    只认最常见的中文表达：N 个月 / N 周 / 半年 / 一年 / 每天 N 小时 / 每周 N 小时 /
    难度关键词 / 技术栈关键词。抽不到就留空，不猜。
    """
    body = normalize(text)
    values: dict = {}

    months = _MONTHS_RE.search(body)
    weeks = _WEEKS_RE.search(body)
    if months:
        number = _cn_number(months.group(1))
        if number:
            values["weeks"] = int(round(number * 4))
    elif weeks:
        number = _cn_number(weeks.group(1))
        if number:
            values["weeks"] = int(number)
    elif _HALF_YEAR_RE.search(body):
        values["weeks"] = 24
    elif _ONE_YEAR_RE.search(body):
        values["weeks"] = 52

    daily = _DAILY_HOURS_RE.search(body)
    weekly = _WEEKLY_HOURS_RE.search(body)
    if daily:
        number = _cn_number(daily.group(1))
        if number:
            values["weekly_hours"] = int(round(number * 7))
    elif weekly:
        number = _cn_number(weekly.group(1))
        if number:
            values["weekly_hours"] = int(number)

    lowered = body.lower()
    for level, words in _LEVEL_WORDS:
        if any(word in lowered for word in words):
            values["level"] = level
            break
    for stack, words in _STACK_WORDS:
        if any(word in lowered for word in words):
            values["stack"] = stack
            break

    goal = _goal_from_text(body, values)
    if goal:
        values["goal"] = goal[:120]

    # 夹到模型约束里（越界就退回默认值，不让 API 变成 422）
    if "weeks" in values:
        values["weeks"] = min(52, max(4, values["weeks"]))
    if "weekly_hours" in values:
        values["weekly_hours"] = min(40, max(1, values["weekly_hours"]))
    return PlanSlots(**values)


_NOISE = re.compile(
    r"(帮我|帮忙|请|麻烦|我想|我要|想要|做个|做一个|制定|安排|一个|一份|一下|计划|"
    r"学习|转行|转|学会|掌握|每天|每周|每小时|个月|月|周|星期|小时|零基础|入门|"
    r"新手|进阶|中级|高级|有基础|小白)"
)


def _goal_from_text(text: str, values: dict) -> str:
    """把时间/难度/动词噪声去掉，剩下的就是目标（不做语义改写）。"""
    body = _MONTHS_RE.sub(" ", text)
    body = _WEEKS_RE.sub(" ", body)
    body = _DAILY_HOURS_RE.sub(" ", body)
    body = _WEEKLY_HOURS_RE.sub(" ", body)
    body = _HALF_YEAR_RE.sub(" ", body)
    body = re.sub(r"[0-9零一二两三四五六七八九十]+", " ", body)
    body = _NOISE.sub(" ", body)
    body = re.sub(r"[的了吧呀啊呢，,。.!！?？、;；:：\s]+", " ", body).strip()
    body = re.sub(r"\s+", " ", body)
    if len(body) < 2:
        return (text or "").strip()[:120]
    return body


def _merge_slots(base: PlanSlots, extra: PlanSlots | None) -> PlanSlots:
    data = base.model_dump()
    if extra is not None:
        for key, value in extra.model_dump().items():
            if value is not None:
                data[key] = value
    return PlanSlots(**data)


async def extract(conn, text: str, llm: LLMClient | None, *, locale: str = "zh-CN",
                  page_context: PageContext | None = None) -> AgentExtractResponse:
    """一句话 → 表单字段（§8.2 第 2 步）。

    **永不因为模型不可用而报错**：LLM 未配置 / 超时 / 输出不合法时用 `regex_slots()`
    兜底，置信度压低到 0.4 左右，让前端知道这是"猜的"。
    """
    body = normalize(text)
    fallback = regex_slots(body)

    decision = None
    if llm is not None and llm.configured:
        try:
            decision = await classify(body, llm, locale=locale)
        except LLMError as exc:
            log.info("event=planner.extract fallback=regex code=%s", exc.code)
        except ValueError:
            log.info("event=planner.extract fallback=regex reason=parse")

    if decision is not None:
        slots = _merge_slots(fallback, decision.slots)
        confidence = decision.confidence
        intent = decision.intent
        needs_clarify = decision.needs_clarify
        options = decision.clarify_options
    else:
        slots = fallback
        confidence = 0.4 if slots.goal else 0.0
        intent = "PLAN_CREATE" if slots.goal else "CLARIFY"
        needs_clarify = not slots.goal
        options = clarify_options(locale) if needs_clarify else []

    if needs_clarify and not options:
        options = clarify_options(locale)
    if not needs_clarify and not slots.goal:
        needs_clarify = True
        options = clarify_options(locale)
        intent = "CLARIFY"

    return AgentExtractResponse(
        intent=intent,
        confidence=min(1.0, max(0.0, confidence)),
        slots=slots,
        needs_clarify=needs_clarify,
        clarify_options=options[:3],
    )
