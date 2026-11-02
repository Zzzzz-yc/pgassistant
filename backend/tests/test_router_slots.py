"""槽位抽取（`router/slots.py`）与 `RESOURCE_SEARCH` 的槽位接入（本轮新增）。

覆盖四件事：

1. 规则槽位：stack / level / topic / weeks / weekly_hours 的口语说法；
2. `merge_slots`：规则优先、模型只填空位、`complete` / `source` 的重算；
3. `query_from_search_text` 的清洗：三条线上回归输入 + 既有「数组」用例；
4. 检索分支降级：无模型时只用规则槽位（0 次模型调用）、规则够用时不补抽、
   规则不完整时才补抽一次，且一次都不把异常漏到 API 层。

**不真调 LLM**：出网全部走 `httpx.MockTransport`（与 `test_agent.py` 同一套手法）。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.api import agent
from app.db.connection import connect
from app.services import llm_client, secrets
from app.services.llm_client import LLMClient, LLMConfig
from app.services.router import query_from_search_text
from app.services.router.classify import extract_slots_llm
from app.services.router.slots import SlotResult, extract_slots, merge_slots

HOST = "http://127.0.0.1:8000"
MOCK_BASE = "https://llm.test/v1"
MOCK_KEY = "sk-test-key"

FRONTEND_SLUG = "frontend/layout/flexbox"


# --------------------------------------------------------------- 夹具
@pytest.fixture(autouse=True)
def _isolate(monkeypatch, db_path):
    """堵住 data/ 里的真实配置与密钥：本模块只测槽位，不该连到真模型。"""
    for name in ("PG_LLM_API_KEY", "PG_LLM_BASE_URL", "PG_LLM_MODEL",
                 "PG_LLM_TEMPERATURE", "PG_LLM_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_client, "_read_config_json", lambda: {})
    monkeypatch.setattr(llm_client, "_read_secret_file", lambda: "")
    monkeypatch.setattr(secrets, "load_secret", lambda name: None)
    monkeypatch.setattr(llm_client, "RATE_LIMIT_BACKOFF", (0.0, 0.0))
    monkeypatch.setattr(llm_client, "NETWORK_BACKOFF", 0.0)
    llm_client._SEMAPHORE = asyncio.Semaphore(2)
    agent.reset_limits()
    _seed_frontend(db_path)
    yield


def _seed_frontend(db_path) -> None:
    """塞一篇 frontend / beginner 的资料：用于证明 `stack` 过滤真的生效。

    同时把 conftest 种子的那篇 cs-basics 资料的 FTS 换成 jieba 的真实切词结果
    （含「闭包」），与 `test_agent.py` 的隔离夹具保持一致，否则「闭包」搜不到。
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE resources_fts SET title_tokens = ?, summary_tokens = ?,"
                " body_tokens = ?, tags_tokens = ?",
                ("数组 闭包", "数组 闭包", "数组 闭包", "数组 闭包"),
            )
            conn.execute(
                """INSERT OR IGNORE INTO resources
                   (slug, title, summary, body_md, body_tokens, stack, category, level,
                    type, duration_min, file_path, content_hash, created_at, updated_at)
                   VALUES (?, '前端布局入门', '用 Flexbox 做页面布局',
                           '正文', '前端 布局 入门', 'frontend', 'layout', 'beginner',
                           'article', 8, 'content/x.md', 'hash-frontend',
                           '2026-10-30', '2026-10-30')""",
                (FRONTEND_SLUG,),
            )
            row = conn.execute("SELECT id FROM resources WHERE slug = ?",
                               (FRONTEND_SLUG,)).fetchone()
            if row:
                conn.execute(
                    "INSERT OR REPLACE INTO resources_fts"
                    " (rowid, title_tokens, summary_tokens, body_tokens, tags_tokens)"
                    " VALUES (?,?,?,?,?)",
                    (row["id"], "前端 布局", "前端 布局", "前端 布局 入门", "前端"),
                )
    finally:
        conn.close()


def headers(token: str) -> dict:
    return {"X-App-Token": token, "Origin": HOST}


def parse_sse(text: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        lines = block.split("\n")
        event_line = [line for line in lines if line.startswith("event: ")]
        data_lines = [line for line in lines if line.startswith("data: ")]
        assert len(event_line) == 1, block
        assert len(data_lines) == 1, block
        frames.append((event_line[0][len("event: "):],
                       json.loads(data_lines[0][len("data: "):])))
    return frames


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "model": "mock-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    })


class MockLLM:
    """记录请求的假上游；`responses` 依次消费，空栈时返回 `{}`（槽位全空）。"""

    def __init__(self, responses=None):
        self.requests: list[httpx.Request] = []
        self.responses = list(responses or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return chat_response("{}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def calls(self) -> int:
        return len(self.requests)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture()
def llm(monkeypatch, db_path):
    """配置好一个假模型（settings 两行 + 环境变量给 api_key，与 `test_agent.py` 一致）。"""
    conn = connect(db_path)
    try:
        with conn:
            for key, value in (("llm.base_url", MOCK_BASE), ("llm.model", "mock-model")):
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)",
                    (key, value, "2026-10-30T00:00:00+00:00"),
                )
    finally:
        conn.close()
    monkeypatch.setenv("PG_LLM_API_KEY", MOCK_KEY)

    holder = {"mock": MockLLM()}

    def factory(config):
        return LLMClient(
            config, client=httpx.AsyncClient(transport=httpx.MockTransport(holder["mock"]))
        )

    monkeypatch.setattr(agent, "LLMClient", factory)
    return holder["mock"]


# --------------------------------------------------------------- 规则槽位
@pytest.mark.parametrize("text, expected", [
    ("我想冲击一下前端岗位，给我找点资料", "frontend"),
    ("有没有讲 React 的文章", "frontend"),
    ("给我推荐点后端的资料", "backend"),
    ("学 Java 还是 Go", "backend"),
    ("有没有讲链表的文章", "cs-basics"),
    ("面试八股怎么背", "interview"),
    ("编程语言基础课", "languages"),
    ("有没有讲地图的文章", None),          # 「地图」里的"图"不是数据结构
    ("帮我找点资料", None),
])
def test_extract_stack(text, expected):
    assert extract_slots(text).stack == expected


@pytest.mark.parametrize("text, expected", [
    ("前端零基础入门", "beginner"),
    ("新手学前端", "beginner"),
    ("前端进阶路线", "intermediate"),
    ("想提升一下", "intermediate"),
    ("高级前端", "advanced"),
    ("资深工程师路线", "advanced"),
    ("我想冲击一下前端岗位", "advanced"),   # 线上回归：冲击岗位 = advanced
])
def test_extract_level(text, expected):
    assert extract_slots(text).level == expected


@pytest.mark.parametrize("text, expected", [
    ("有没有讲闭包的文章", "闭包"),
    ("有没有讲链表的文章", "链表"),
    ("有没有讲React的文章", "React"),
    ("React 的资料", "React"),
    ("给我推荐点后端的资料", "后端"),
    ("我想冲击一下前端岗位，给我找点资料", "前端"),
])
def test_extract_topic(text, expected):
    assert extract_slots(text).topic == expected


def test_topic_never_contains_job_boilerplate():
    """「岗位」是噪声词：既不能进 topic，也不能进清洗后的查询词。"""
    found = extract_slots("前端岗位")
    assert found.topic == "前端"
    assert "岗位" not in (found.topic or "")
    assert query_from_search_text("前端岗位") == "前端"


@pytest.mark.parametrize("text, weeks, hours", [
    ("帮我做个12周的前端学习计划", 12, None),
    ("三个月转前端，每天两小时", 12, 14),
    ("半年学会后端", 24, None),
    ("每周10小时学算法", None, 10),
    ("12 周、每周 1 小时的前端计划", 12, 1),
    ("帮我找点资料", None, None),
])
def test_extract_weeks_and_hours(text, weeks, hours):
    found = extract_slots(text)
    assert found.weeks == weeks
    assert found.weekly_hours == hours


def test_complete_only_when_stack_or_topic():
    assert extract_slots("我想学前端").complete is True
    assert extract_slots("有没有讲闭包的文章").complete is True
    assert extract_slots("帮我找点资料").complete is False
    assert extract_slots("").complete is False
    assert extract_slots("").source == "rule"


# --------------------------------------------------------------- 合并
def test_merge_slots_rule_wins_and_fills_gaps():
    rule = SlotResult(stack="backend", topic=None, level=None, weeks=None)
    extra = SlotResult(stack="frontend", level="advanced", goal="闭包",
                       topic="闭包", weeks=12, weekly_hours=10, source="llm")
    merged = merge_slots(rule, extra)
    assert merged.stack == "backend"          # 规则优先，模型改不了
    assert merged.topic == "闭包"             # 空位由模型补
    assert merged.level == "advanced"
    assert merged.weeks == 12 and merged.weekly_hours == 10
    assert merged.complete is True
    assert merged.source == "llm"             # 确实用到了模型结果


def test_merge_slots_keeps_rule_source_when_model_adds_nothing():
    rule = extract_slots("有没有讲闭包的文章")
    merged = merge_slots(rule, SlotResult())
    assert merged.source == "rule"
    assert merged.stack is None and merged.topic == "闭包"


def test_merge_slots_drops_invalid_stack_and_recomputes_complete():
    merged = merge_slots(SlotResult(), SlotResult(stack="kafka", level="expert"))
    assert merged.stack is None and merged.level is None
    assert merged.complete is False


# --------------------------------------------------------------- 查询词清洗
@pytest.mark.parametrize("text, expected", [
    ("有没有讲数组的文章", "数组"),                              # 既有用例，不许改坏
    ("有没有讲闭包的文章", "闭包"),
    ("我想冲击一下前端岗位，给我找点资料", "前端"),
    ("给我推荐点后端的资料", "后端"),
])
def test_query_from_search_text(text, expected):
    assert query_from_search_text(text) == expected


# --------------------------------------------------------------- 检索分支
def _post_chat(client, token, message):
    resp = client.post("/api/agent/chat", headers=headers(token),
                       json={"message": message})
    assert resp.status_code == 200, resp.text
    return parse_sse(resp.text)


def test_chat_search_uses_rule_slots_without_model(client, token):
    """无模型（未配 LLM）：只用规则槽位，检索照常返回，且不因为 level 过滤而空。"""
    frames = _post_chat(client, token, "我想冲击一下前端岗位，给我找点资料")
    assert frames[0][1]["intent"] == "RESOURCE_SEARCH"
    assert frames[-1][1]["kind"] == "search"
    result = frames[-1][1]["result"]
    assert result["total"] >= 1
    assert all(item["stack"] == "frontend" for item in result["items"])
    assert result["query"] == "前端"


def test_chat_search_without_model_degrades_instead_of_error(client, token, db_path):
    """槽位不完整 + 没模型：不调用模型、不发 error 帧，按现状返回（可能 0 条）。"""
    conn = connect(db_path)
    try:
        assert llm_client.load_llm_config(conn) is None   # 确实没有模型
    finally:
        conn.close()
    frames = _post_chat(client, token, "帮我找点资料")
    assert [name for name, _ in frames][-1] == "done"
    assert frames[-1][1]["kind"] == "search"


def test_chat_search_complete_slots_skip_llm(client, token, llm):
    """规则已经够用（有 topic）：一次模型调用都不该发生。"""
    frames = _post_chat(client, token, "有没有讲闭包的文章")
    assert frames[-1][1]["kind"] == "search"
    assert frames[-1][1]["result"]["total"] >= 1
    assert llm.calls == 0


def test_chat_search_incomplete_slots_ask_llm_once(client, token, llm):
    """规则抽不全且有模型：补抽一次，补出来的 stack 真的用于过滤。"""
    llm.responses.append(chat_response(json.dumps(
        {"stack": "frontend", "level": None, "goal": "前端布局", "topic": None,
         "weeks": None, "weekly_hours": None}, ensure_ascii=False)))
    frames = _post_chat(client, token, "帮我找点资料")
    assert llm.calls == 1
    assert llm.body(0)["response_format"] == {"type": "json_object"}
    assert "USER_TEXT" in llm.body(0)["messages"][1]["content"]
    result = frames[-1][1]["result"]
    assert result["total"] >= 1
    assert all(item["stack"] == "frontend" for item in result["items"])


def test_chat_search_llm_failure_falls_back_to_rules(client, token, llm):
    """模型回 503：返回规则结果 + 不发 error 帧（槽位补抽不能把检索打成 500）。"""
    llm.responses.append(httpx.Response(503))
    frames = _post_chat(client, token, "我想冲击一下前端岗位，给我找点资料")
    assert frames[-1][0] == "done"
    assert frames[-1][1]["kind"] == "search"
    assert frames[-1][1]["result"]["total"] >= 1


# --------------------------------------------------------------- L2 补抽
def test_extract_slots_llm_parses_and_sanitizes():
    mock = MockLLM([chat_response(json.dumps(
        {"stack": "frontend", "level": "advanced", "goal": "冲击前端岗位",
         "topic": "React", "weeks": 12, "weekly_hours": 10}, ensure_ascii=False))])
    client = LLMClient(LLMConfig(base_url=MOCK_BASE, api_key=MOCK_KEY, model="mock-model"),
                       client=httpx.AsyncClient(transport=httpx.MockTransport(mock)))
    try:
        found = asyncio.run(extract_slots_llm("冲击前端岗位", client))
    finally:
        asyncio.run(client.aclose())
    assert found is not None
    assert (found.stack, found.level, found.topic) == ("frontend", "advanced", "React")
    assert (found.weeks, found.weekly_hours) == (12, 10)
    assert found.complete is True and found.source == "llm"


def test_extract_slots_llm_returns_none_on_garbage_without_raising():
    mock = MockLLM([chat_response("这不是 JSON")])
    client = LLMClient(LLMConfig(base_url=MOCK_BASE, api_key=MOCK_KEY, model="mock-model"),
                       client=httpx.AsyncClient(transport=httpx.MockTransport(mock)))
    try:
        found = asyncio.run(extract_slots_llm("帮我找点资料", client))
    finally:
        asyncio.run(client.aclose())
    assert found is None
