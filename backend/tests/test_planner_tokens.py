"""计划生成的输出预算回归（C3 延伸）。

两个缺陷点各一组用例：

1. `_plan_max_tokens` 读 `LLMClient.config`（不是写死的 4096），并夹取 4096–16384；
2. 输出被 `max_tokens` 截断时不再直接失败：第一次命中截断 → 用压缩指令重试，
   两次都截断才抛 `PlanInvalid`，且错误里能看出是长度问题。

全部用 stub 客户端，不真调 LLM、不需要真实 key。
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from app.db.connection import connect
from app.services import llm_client, plan_validate, planner
from app.services.llm_client import LLMConfig, LLMFormatUnsupported, LLMResponse

MOCK_BASE = "https://llm.test/v1"
MOCK_KEY = "sk-test-key"

#: 被 max_tokens 截断的典型形状：字符串没闭合、正文停在半个条目上。
TRUNCATED_JSON = (
    '{"title": "转前端计划", "goal": "三个月转前端", "weeks": 4, "weekly_hours": 1,'
    ' "stages": [{"title": "基础", "weeks": 2, "items": [{"title": "xxx'
)

#: P2 的核心形状：截断恰好停在 `}` 边界。正文以 `}` 收尾（`_looks_truncated`
#: 会当成「输出完整」），但外层对象其实还差一个键，json 报 `Expecting property name`。
BRACE_BOUNDARY_JSON = (
    '{"title": "转前端计划", "goal": "三个月转前端", "weeks": 4, "weekly_hours": 1,}'
)

#: 校验能过但条目不足的草稿（合法 JSON，只是内容不全）。
THIN_DRAFT = {
    "title": "转前端计划",
    "goal": "三个月转前端",
    "weeks": 4,
    "weekly_hours": 1,
    "stages": [
        {"title": "基础", "weeks": 4,
         "items": [{"title": "读 HTML", "resource_slug": None,
                    "est_minutes": 40, "note": "自定义任务"}]},
    ],
}


def _valid_draft() -> dict:
    """4 周 × 每周 1 小时 → 目标 240 分钟；6 条 × 40 分钟刚好命中。"""
    def item(title: str) -> dict:
        return {"title": title, "resource_slug": None, "est_minutes": 40,
                "note": "自定义任务"}

    return {
        "title": "转前端计划",
        "goal": "三个月转前端",
        "weeks": 4,
        "weekly_hours": 1,
        "stages": [
            {"title": "基础", "weeks": 2, "description": "",
             "items": [item("读 HTML"), item("读 CSS"), item("写静态页")]},
            {"title": "进阶", "weeks": 2, "description": "",
             "items": [item("学 JS"), item("做组件"), item("做小项目")]},
        ],
    }


class StubLLM:
    """只实现 `chat()` 的假客户端：依次回放 replies，并记下每次的入参。"""

    def __init__(self, replies: list, *, max_tokens: int = 8192) -> None:
        self.config = LLMConfig(
            base_url=MOCK_BASE, api_key=MOCK_KEY, model="mock-model",
            max_tokens=max_tokens,
        )
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def chat(self, messages, *, max_tokens, temperature=None,
                   response_format=None, timeout=None):
        self.calls.append({
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": response_format,
        })
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        finish_reason = None
        if isinstance(reply, tuple):            # (正文, 上游 finish_reason)
            reply, finish_reason = reply
        return LLMResponse(
            text=reply, tokens_in=10, tokens_out=10, model="mock-model",
            finish_reason=finish_reason,
        )


def _messages_text(call: dict) -> str:
    return json.dumps(call["messages"], ensure_ascii=False)


# ----------------------------------------------------- max_tokens 取值 / 夹取
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (8192, 8192),        # 用户设置原样生效
        (1024, 4096),        # 低于下限：会截断 40 条目，抬到下限
        (99999, 16384),      # 高于上限：上游会 400，压到上限
        (None, 4096),        # 配置缺失：下限兜底
    ],
)
def test_plan_max_tokens_clamps_configured_budget(configured, expected):
    client = llm_client.LLMClient(
        LLMConfig(base_url=MOCK_BASE, api_key=MOCK_KEY, model="mock-model",
                  max_tokens=configured)
    )
    try:
        assert planner._plan_max_tokens(client) == expected
    finally:
        asyncio.run(client.aclose())


def test_plan_max_tokens_falls_back_without_config():
    """未配置（`LLMClient(None)`）不让取值函数抛异常。"""
    client = llm_client.LLMClient(None)
    try:
        assert planner._plan_max_tokens(client) == planner._PLAN_MAX_TOKENS_MIN
    finally:
        asyncio.run(client.aclose())


# ----------------------------------------------------- 截断自愈
def test_truncated_output_retries_with_compact_instruction(db_path):
    conn = connect(db_path)
    try:
        req = planner.AgentPlanDraftRequest(goal="前端", weeks=4, weekly_hours=1)
        llm = StubLLM([
            TRUNCATED_JSON,
            json.dumps(_valid_draft(), ensure_ascii=False),
        ], max_tokens=8192)

        draft = asyncio.run(planner.generate(conn, req, llm))

        assert len(llm.calls) == 2                       # 命中截断 → 用满 2 次重试
        assert [call["max_tokens"] for call in llm.calls] == [8192, 8192]
        assert llm.calls[0]["response_format"] == {"type": "json_object"}
        second = _messages_text(llm.calls[1])
        assert "截断" in second                          # 压缩指令，而不是普通校验回填
        assert "阶段 ≤4" in second
        assert "条目 ≤20 条" in second
        assert "title ≤20 字" in second
        assert sum(len(stage.items) for stage in draft.stages) == 6
    finally:
        conn.close()


def test_two_truncated_outputs_raise_length_aware_plan_invalid(db_path):
    conn = connect(db_path)
    try:
        req = planner.AgentPlanDraftRequest(goal="前端", weeks=4, weekly_hours=1)
        llm = StubLLM([TRUNCATED_JSON, TRUNCATED_JSON], max_tokens=4096)

        with pytest.raises(plan_validate.PlanInvalid) as info:
            asyncio.run(planner.generate(conn, req, llm))

        assert len(llm.calls) == 2
        assert [call["max_tokens"] for call in llm.calls] == [4096, 4096]
        errors = "；".join(info.value.errors)
        assert "长度限制截断" in errors
        assert "max_tokens=4096" in errors
        assert info.value.suggestions                    # 422 仍带兜底素材
    finally:
        conn.close()


def test_prompt_only_fallback_keeps_same_budget(db_path):
    """`response_format` 不被上游接受时，降级调用的预算不变。"""
    conn = connect(db_path)
    try:
        req = planner.AgentPlanDraftRequest(goal="前端", weeks=4, weekly_hours=1)
        llm = StubLLM([
            LLMFormatUnsupported("上游不接受 response_format"),
            json.dumps(_valid_draft(), ensure_ascii=False),
        ], max_tokens=8192)

        draft = asyncio.run(planner.generate(conn, req, llm))

        assert [call["max_tokens"] for call in llm.calls] == [8192, 8192]
        assert llm.calls[0]["response_format"] is not None
        assert llm.calls[1]["response_format"] is None
        assert draft.stages
    finally:
        conn.close()


# ----------------------------------------------------- finish_reason（P2 修复）
def _parse_chat(data):
    """直接走 `_parse_chat`：不建 httpx 客户端、不触网。"""
    config = LLMConfig(base_url=MOCK_BASE, api_key=MOCK_KEY, model="mock-model")
    client = object.__new__(llm_client.LLMClient)
    return client._parse_chat(data, [], config)


def test_parse_chat_exposes_finish_reason():
    data = {
        "model": "mock-model",
        "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }
    response = _parse_chat(data)

    assert response.finish_reason == "length"
    # 既有字段只做加法：文本与 token 计数不受影响
    assert (response.text, response.tokens_in, response.tokens_out) == ("{}", 3, 4)


@pytest.mark.parametrize("value", [None, 123, ["length"], {"reason": "length"}])
def test_parse_chat_non_string_finish_reason_is_none(value):
    """上游字段缺失 / 非字符串：收敛成 None，**不抛错**。"""
    data = {
        "choices": [{"message": {"content": "{}"}, "finish_reason": value}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }
    assert _parse_chat(data).finish_reason is None


def test_parse_chat_without_finish_reason_is_none():
    """上游完全不返回 finish_reason（老服务商 / stub）也不抛错。"""
    data = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }
    assert _parse_chat(data).finish_reason is None


def test_finish_reason_length_with_valid_draft_is_accepted_in_one_call(db_path):
    """finish_reason=length 但草稿合法且通过校验：不接受多余重试。"""
    conn = connect(db_path)
    try:
        req = planner.AgentPlanDraftRequest(goal="前端", weeks=4, weekly_hours=1)
        llm = StubLLM([
            (json.dumps(_valid_draft(), ensure_ascii=False), "length"),
        ])

        draft = asyncio.run(planner.generate(conn, req, llm))

        assert len(llm.calls) == 1                 # 关键：1 次调用，不压缩重试
        assert sum(len(stage.items) for stage in draft.stages) == 6
    finally:
        conn.close()


def test_finish_reason_length_validation_failure_retries_compact(db_path, caplog):
    """JSON 合法但**校验失败**（条目不足）+ finish_reason=length → 压缩重试。"""
    caplog.set_level(logging.WARNING, logger="pgassistant.planner")
    conn = connect(db_path)
    try:
        req = planner.AgentPlanDraftRequest(goal="前端", weeks=4, weekly_hours=1)
        llm = StubLLM([
            (json.dumps(THIN_DRAFT, ensure_ascii=False), "length"),
            (json.dumps(_valid_draft(), ensure_ascii=False), "stop"),
        ])

        draft = asyncio.run(planner.generate(conn, req, llm))

        assert len(llm.calls) == 2
        assert "截断" in _messages_text(llm.calls[1])   # 压缩指令，不是普通回填
        assert "条目 ≤20 条" in _messages_text(llm.calls[1])
        assert "by=finish_reason" in caplog.text
        assert draft.stages
    finally:
        conn.close()


def test_brace_boundary_truncation_is_detected_by_finish_reason(db_path, caplog):
    """P2 核心场景：截断停在 `}` 边界，`Expecting property name`。

    正文以 `}` 收尾 → `_looks_truncated` 判不出来（这是修复前的唯一信号），
    现在由 `finish_reason="length"` 兜住，第一次就改走压缩重试。
    """
    with pytest.raises(ValueError) as info:
        json.loads(BRACE_BOUNDARY_JSON)
    assert "expecting property name" in str(info.value).lower()
    assert BRACE_BOUNDARY_JSON.rstrip().endswith("}")
    assert planner._looks_truncated(BRACE_BOUNDARY_JSON, info.value) is False

    caplog.set_level(logging.WARNING, logger="pgassistant.planner")
    conn = connect(db_path)
    try:
        req = planner.AgentPlanDraftRequest(goal="前端", weeks=4, weekly_hours=1)
        llm = StubLLM([
            (BRACE_BOUNDARY_JSON, "length"),
            (json.dumps(_valid_draft(), ensure_ascii=False), "stop"),
        ])

        draft = asyncio.run(planner.generate(conn, req, llm))

        assert len(llm.calls) == 2
        second = _messages_text(llm.calls[1])
        assert "截断" in second
        assert "阶段 ≤4" in second
        assert "by=finish_reason" in caplog.text
        assert draft.stages
    finally:
        conn.close()


@pytest.mark.parametrize(("raw", "marker"), [
    ('{"title": "x", "stages": [{"title": "a"}],', "expecting property name"),
    ('{"title": ', "expecting value"),
])
def test_looks_truncated_covers_partial_key_markers(raw, marker):
    """新增的 JSON 报错标记：正文停在键名中间 / 值之前。"""
    with pytest.raises(ValueError) as info:
        json.loads(raw)

    assert marker in str(info.value).lower()
    assert planner._looks_truncated(raw, info.value) is True


def test_chat_finish_reason_length_flows_into_planner_without_retry(db_path):
    """端到端：真实 `LLMClient.chat` 解析上游 finish_reason，planner 只调 1 次。"""
    payload = {
        "model": "mock-model",
        "choices": [{
            "message": {"content": json.dumps(_valid_draft(), ensure_ascii=False)},
            "finish_reason": "length",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200},
    }
    seen: list[dict] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    conn = connect(db_path)
    try:
        async def go():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as http_client:
                client = llm_client.LLMClient(
                    LLMConfig(base_url=MOCK_BASE, api_key=MOCK_KEY,
                              model="mock-model", max_tokens=8192),
                    client=http_client,
                )
                response = await client.chat(
                    [{"role": "user", "content": "[1, 2]"}], max_tokens=8192,
                )
                assert response.finish_reason == "length"
                req = planner.AgentPlanDraftRequest(
                    goal="前端", weeks=4, weekly_hours=1
                )
                return await planner.generate(conn, req, client)

        draft = asyncio.run(go())

        # 1 次是上面的手动 chat（验证解析），1 次是 planner.generate；
        # 若 planner 因 finish_reason=length 多跑一次压缩重试，这里会是 3。
        assert len(seen) == 2
        assert draft.stages
    finally:
        conn.close()
