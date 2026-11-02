"""第三层检索：零结果 → LLM 重写查询 → 再检索一次。

全部不出网：假上游走 `httpx.MockTransport`（与 `test_agent.py` 同口径）。
覆盖验收要求的 5 条（外加一条「消息过短不重试」，保护触发条件的第 4 项）：

1. 空结果触发：规则查询 0 条 → stub 重写成「闭包」→ 第二轮命中，
   `llm.calls == 1`、日志有 `search_retry`（first_total=0 / second_total>0 / llm_used=true）；
2. 不触发：第一轮就有结果 → `llm.calls == 0`、没有 `search_retry` 日志；
3. 无模型：第一轮 0 条 → `llm.calls == 0`、返回空、不抛错、无 error 帧；
4. 重写失败（脏 JSON / `LLMError`）→ 返回第一轮空结果、HTTP 200、无 error 帧；
5. 最多一次：重写后仍空 → `llm.calls <= 1`（`_RETRY_LIMIT = 1`，不得二次重试）。
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from app.api import agent
from app.db.connection import connect
from app.services import llm_client, secrets
from app.services.llm_client import LLMClient

HOST = "http://127.0.0.1:8000"
MOCK_BASE = "https://llm.test/v1"
MOCK_KEY = "sk-test-key"
SEEDED_SLUG = "cs-basics/array-linkedlist/array"

#: 规则路径 0 条（主题词 kafka 站内不存在），但可被重写救回。
ZERO_MESSAGE = "有没有讲 kafka 的文章"
#: 规则路径第一轮就命中（种子库的 FTS 词是「数组 闭包」）。
HIT_MESSAGE = "有没有讲数组的文章"


# --------------------------------------------------------------- 夹具
@pytest.fixture(autouse=True)
def _isolate(monkeypatch, db_path):
    """每个用例：清环境变量、堵住 data/ 里的真实配置、重置信号量与令牌桶。"""
    for name in (
        "PG_LLM_API_KEY", "PG_LLM_BASE_URL", "PG_LLM_MODEL", "PG_LLM_TEMPERATURE",
        "PG_LLM_MAX_TOKENS", "PG_EMBEDDING_BASE_URL", "PG_EMBEDDING_MODEL",
        "PG_EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_client, "_read_config_json", lambda: {})
    monkeypatch.setattr(llm_client, "_read_secret_file", lambda: "")
    monkeypatch.setattr(secrets, "load_secret", lambda name: None)
    monkeypatch.setattr(llm_client, "RATE_LIMIT_BACKOFF", (0.0, 0.0))
    monkeypatch.setattr(llm_client, "NETWORK_BACKOFF", 0.0)
    llm_client._SEMAPHORE = asyncio.Semaphore(2)
    agent.reset_limits()

    # 种子库的 FTS 行是手写的 "数 组"；换成 jieba 的真实输出，让「数组」「闭包」都命中。
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE resources_fts SET title_tokens = ?, summary_tokens = ?,"
                " body_tokens = ?, tags_tokens = ?",
                ("数组 闭包", "数组 闭包", "数组 闭包", "数组 闭包"),
            )
    finally:
        conn.close()
    yield


class MockLLM:
    """记录请求的假上游。`responses` 依次消费；元素可以是 Response 或异常。"""

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


def chat_response(content: str, *, status: int = 200) -> httpx.Response:
    payload = {
        "model": "mock-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    return httpx.Response(status, json=payload)


def rewrite_payload(query: str, *, stack=None, topic=None) -> str:
    return json.dumps({"query": query, "stack": stack, "topic": topic or query,
                       "reason": "test"}, ensure_ascii=False)


@pytest.fixture()
def llm(monkeypatch, db_path):
    """配置好一个假模型（settings 两行 + 环境变量给 api_key），并统计出网次数。"""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)",
                ("llm.base_url", MOCK_BASE, "2026-11-02T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)",
                ("llm.model", "mock-model", "2026-11-02T00:00:00+00:00"),
            )
    finally:
        conn.close()
    monkeypatch.setenv("PG_LLM_API_KEY", MOCK_KEY)

    holder = MockLLM()
    monkeypatch.setattr(
        agent, "LLMClient",
        lambda config: LLMClient(
            config, client=httpx.AsyncClient(transport=httpx.MockTransport(holder))
        ),
    )
    return holder


class NoModelLLM:
    """无模型时 `agent_chat` 仍会构造一个未配置的 client；它绝不该被调用。"""

    configured = False

    def __init__(self, config=None):
        self.calls = 0

    async def chat(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("无模型时不允许调用 LLM.chat")

    def stream(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("无模型时不允许调用 LLM.stream")

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def no_model(monkeypatch):
    holder: list[NoModelLLM] = []

    def factory(config=None):
        holder.append(NoModelLLM(config))
        return holder[-1]

    monkeypatch.setattr(agent, "LLMClient", factory)
    return holder


# --------------------------------------------------------------- 工具
def headers(token: str) -> dict:
    return {"X-App-Token": token, "Origin": HOST}


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """按 §4.1 切帧并解析；顺带断言"一帧只有一行 data:"。"""
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


def search_retry_logs(caplog) -> list[dict]:
    """摘出 `pgassistant.agent` 里的 `search_retry` 结构化日志。"""
    found = []
    for record in caplog.records:
        if record.name != "pgassistant.agent":
            continue
        try:
            payload = json.loads(record.getMessage())
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("event") == "search_retry":
            found.append(payload)
    return found


def chat(client, token: str, message: str):
    return client.post("/api/agent/chat", headers=headers(token),
                       json={"message": message})


# --------------------------------------------------------------- 1. 空结果触发
def test_zero_result_triggers_rewrite_and_second_search_hits(client, token, llm, caplog):
    llm.responses.append(chat_response(rewrite_payload("闭包")))

    with caplog.at_level(logging.INFO, logger="pgassistant.agent"):
        resp = chat(client, token, ZERO_MESSAGE)

    assert resp.status_code == 200
    frames = parse_sse(resp.text)
    assert [name for name, _ in frames] == ["intent", "done"]
    assert frames[0][1]["intent"] == "RESOURCE_SEARCH"
    assert frames[0][1]["source"] == "rule"

    done = frames[-1][1]
    assert done["kind"] == "search"
    assert done["result"]["total"] > 0
    assert done["result"]["items"][0]["slug"] == SEEDED_SLUG
    assert llm.calls == 1                       # 只有重写这一次

    logs = search_retry_logs(caplog)
    assert len(logs) == 1
    assert logs[0]["trigger"] == "zero_result"
    assert logs[0]["rewritten"] is True
    assert logs[0]["first_total"] == 0
    assert logs[0]["second_total"] > 0
    assert logs[0]["llm_used"] is True


# --------------------------------------------------------------- 2. 不触发
def test_first_round_hit_does_not_call_llm(client, token, llm, caplog):
    with caplog.at_level(logging.INFO, logger="pgassistant.agent"):
        resp = chat(client, token, HIT_MESSAGE)

    frames = parse_sse(resp.text)
    assert [name for name, _ in frames] == ["intent", "done"]
    assert frames[-1][1]["result"]["total"] > 0
    assert llm.calls == 0
    assert search_retry_logs(caplog) == []


# --------------------------------------------------------------- 3. 无模型
def test_no_model_returns_empty_without_calls(client, token, no_model, caplog):
    with caplog.at_level(logging.INFO, logger="pgassistant.agent"):
        resp = chat(client, token, ZERO_MESSAGE)

    assert resp.status_code == 200
    frames = parse_sse(resp.text)
    assert [name for name, _ in frames] == ["intent", "done"]   # 没有 error 帧
    assert frames[0][1]["intent"] == "RESOURCE_SEARCH"
    done = frames[-1][1]
    assert done["kind"] == "search"
    assert done["result"]["total"] == 0
    assert len(no_model) == 1
    assert no_model[0].calls == 0
    assert search_retry_logs(caplog) == []


# --------------------------------------------------------------- 4. 重写失败
@pytest.mark.parametrize("failure", [
    pytest.param(chat_response("这不是 JSON"), id="dirty-json"),
    pytest.param(chat_response("[]"), id="not-an-object"),
    pytest.param(httpx.ReadTimeout("slow"), id="llm-timeout"),
])
def test_rewrite_failure_returns_first_empty(client, token, llm, failure):
    llm.responses.append(failure)

    resp = chat(client, token, ZERO_MESSAGE)

    assert resp.status_code == 200
    frames = parse_sse(resp.text)
    assert [name for name, _ in frames] == ["intent", "done"]   # 无 error 帧
    done = frames[-1][1]
    assert done["kind"] == "search"
    assert done["result"]["total"] == 0
    assert done["result"]["query"] == "kafka"                   # 仍是第一轮的查询词
    assert llm.calls == 1                                       # 只尝试过重写这一次


# --------------------------------------------------------------- 5. 最多一次
def test_retry_runs_at_most_once_when_rewrite_still_empty(client, token, llm, caplog):
    # 同一句重写后仍是 0 条：第二次机会用掉就收手，不得再来一轮。
    llm.responses.append(chat_response(rewrite_payload("zookeeper")))

    with caplog.at_level(logging.INFO, logger="pgassistant.agent"):
        resp = chat(client, token, ZERO_MESSAGE)

    resp_frames = parse_sse(resp.text)
    assert [name for name, _ in resp_frames] == ["intent", "done"]  # 无 error 帧
    assert resp_frames[-1][1]["result"]["total"] == 0
    assert llm.calls <= 1
    assert llm.calls == 1

    logs = search_retry_logs(caplog)
    assert len(logs) == 1
    assert logs[0]["rewritten"] is True
    assert logs[0]["first_total"] == 0
    assert logs[0]["second_total"] == 0


def test_short_message_never_retries():
    """触发条件第 4 项：单字 / 空噪声不重写（llm.calls == 0）。"""
    # 单字不会命中检索规则（L1 判 CLARIFY），这里直接测纯触发判据本身。
    assert agent._RETRY_LIMIT == 1
    assert agent._RETRY_MIN_CHARS == 2
    dummy = NoModelLLM()
    dummy.configured = True
    assert agent._retry_eligible(object(), "嗯", dummy, 0) is False
    assert agent._retry_eligible(object(), "有没有讲 kafka 的文章", dummy, 0) is True
    assert agent._retry_eligible(object(), "有没有讲 kafka 的文章", dummy, 3) is False
