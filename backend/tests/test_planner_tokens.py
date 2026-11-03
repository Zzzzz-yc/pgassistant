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
        return LLMResponse(text=reply, tokens_in=10, tokens_out=10, model="mock-model")


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
