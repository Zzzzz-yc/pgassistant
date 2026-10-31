"""L2：让模型把一句话分类成严格 JSON（§7.5）。

三件硬要求：

1. **`response_format={"type":"json_object"}` 优先**；上游不支持（400）时降级为
   「只输出 JSON」的纯提示词重试，不把用户的请求变成 500；
2. 输出必须过 Pydantic；**失败重试 1 次**，并把错误信息回填给模型；
3. 两次都失败 → `RouteDecision(intent=CLARIFY, confidence=0.0, source="fallback")`，
   走澄清卡片（§7.2 兜底）。分类任务的 `max_tokens` 固定 512（§7.10）。

上游故障（401 / 429 / 超时 / 网络 / 未配置）**不在这里吞掉**：那是 HTTP 层的事，
`api/agent.py` 按 MODULES §4.3 的映射表转成错误码（SSE 里是 `event: error`）。
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...prompts import load_prompt
from ...schemas.models import Intent, PlanSlots
from ..llm_client import (
    LLMBadOutput,
    LLMClient,
    LLMError,
    LLMFormatUnsupported,
    strip_fences,
)
from . import RouteDecision, clarify_options, normalize, plan_clarify_options

log = logging.getLogger("pgassistant.router.classify")

#: 分类任务的输出预算（§7.10：分类 512）。
MAX_TOKENS = 512

#: 置信度下限：低于它一律当"意图不明"（§7.2 兜底）。
MIN_CONFIDENCE = 0.55

#: 把校验错误回填给模型时的提示（不包含用户原文，避免二次泄露）。
RETRY_NOTE = (
    "上一次输出不合法：%s\n"
    "请只输出一个符合 schema 的 JSON 对象，不要解释、不要代码围栏。"
)


class _Decision(BaseModel):
    """L2 输出的内部校验模型（**不是** API 契约模型，只用来兜住脏数据）。"""

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    slots: PlanSlots | None = None
    needs_clarify: bool = False
    clarify_options: list[str] = Field(default_factory=list, max_length=3)


def _parse(text: str) -> _Decision:
    """剥围栏 → `json.loads` → Pydantic。任何一步失败都抛 `ValueError`。"""
    body = strip_fences(text)
    if not body:
        raise ValueError("空输出")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError("不是合法 JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise ValueError("顶层必须是 JSON 对象")
    try:
        return _Decision.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_short_errors(exc)) from exc


def _short_errors(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:4]:
        location = ".".join(str(part) for part in error["loc"])
        parts.append("%s: %s" % (location or "<root>", error["msg"]))
    return "；".join(parts)


async def _ask(llm: LLMClient, messages: list[dict]) -> str:
    """一次分类调用：json_object 优先，不支持就降级为纯提示词。"""
    try:
        response = await llm.chat(
            messages,
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except LLMFormatUnsupported:
        log.info("event=router.classify fallback=prompt_only reason=response_format")
        response = await llm.chat(messages, max_tokens=MAX_TOKENS, temperature=0.0)
    return response.text


async def classify(text: str, llm: LLMClient, *, locale: str = "zh-CN") -> RouteDecision:
    """L2 分类。网络/鉴权/限流错误向上抛；只有"输出不可用"才降级为澄清。"""
    body = normalize(text)
    system = load_prompt("classify")
    base_messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "USER_TEXT:\n" + body + "\n\nLOCALE: " + (locale or "zh-CN")},
    ]

    last_error = ""
    last_reply = ""
    for attempt in (0, 1):
        messages = list(base_messages)
        if attempt:
            messages += [
                {"role": "assistant", "content": last_reply[:600]},
                {"role": "user", "content": RETRY_NOTE % last_error},
            ]
        try:
            last_reply = await _ask(llm, messages)
        except LLMBadOutput as exc:          # 上游正文不可用：等同"输出非 JSON"
            last_error = exc.message
            continue
        except LLMError:
            raise                            # 401/429/超时/网络：交给 HTTP 映射
        try:
            return _to_route(_parse(last_reply), locale)
        except ValueError as exc:
            last_error = str(exc)[:400]
            log.info("event=router.classify attempt=%d invalid=%s", attempt + 1, last_error)

    log.warning("event=router.classify fallback=clarify after 2 attempts")
    return RouteDecision(
        intent=Intent.CLARIFY,
        confidence=0.0,
        source="fallback",
        needs_clarify=True,
        clarify_options=clarify_options(locale),
    )


def _to_route(decision: _Decision, locale: str) -> RouteDecision:
    """把模型输出收敛成 `RouteDecision`；置信度不足统一走 CLARIFY（§7.2）。"""
    options = [item.strip() for item in decision.clarify_options if item.strip()][:3]
    # 置信度不足 / 模型自己说 CLARIFY → CLARIFY；**只是信息不足**的计划意图保留
    # PLAN_CREATE + needs_clarify，好让 API 层用方向性提问的固定话术追问（C2）。
    lowered = decision.intent == Intent.CLARIFY or decision.confidence < MIN_CONFIDENCE
    needs_clarify = lowered or decision.needs_clarify
    if needs_clarify and not options and decision.intent == Intent.PLAN_CREATE:
        # 计划意图的信息不足：选项要用"顺着学习方向"的三条，而不是通用那三条（C2）。
        topic = (decision.slots.goal if decision.slots else "") or ""
        options = plan_clarify_options(locale, topic)
    if needs_clarify:
        return RouteDecision(
            intent=decision.intent if not lowered else Intent.CLARIFY,
            confidence=min(1.0, max(0.0, decision.confidence)),
            source="llm",
            slots=decision.slots,
            needs_clarify=True,
            clarify_options=options or clarify_options(locale),
        )
    return RouteDecision(
        intent=decision.intent,
        confidence=min(1.0, max(0.0, decision.confidence)),
        source="llm",
        slots=decision.slots,
        needs_clarify=False,
        clarify_options=options,
    )
