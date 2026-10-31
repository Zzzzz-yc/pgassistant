"""LLM 上游错误可读性 + 连接探针测试（M4，Round 16）。

全部出网走 `httpx.MockTransport`，**不真调外部 API**。覆盖：
- 400 带上游 `error.message` / `error.code` / `error.type`；
- 404（`base_url` 缺 `/v1` 的典型症状）与纯文本 / HTML 响应体；
- 401 / 403 密钥类；429 退避语义与错误码不变；5xx 语义不变；
- 响应体超长截断（message ≤300 字原因、detail.body ≤500 字符）；
- `probe()` 失败也返回可读 message + 按状态码 hint，且探针 `max_tokens` 足够推理型模型。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services import llm_client
from app.services.llm_client import LLMClient, LLMConfig

CONFIG = LLMConfig(
    base_url="https://llm.test/v1",
    api_key="sk-test-0000",
    model="mock-model",
    temperature=0.3,
    max_tokens=1024,
)


def make_client(handler) -> LLMClient:
    transport = httpx.MockTransport(handler)
    return LLMClient(CONFIG, client=httpx.AsyncClient(transport=transport))


def run_chat(client: LLMClient):
    async def go():
        try:
            return await client.chat([{"role": "user", "content": "ping"}], max_tokens=16)
        finally:
            await client.aclose()

    return asyncio.run(go())


def run_probe(client: LLMClient, timeout: float = 10.0):
    async def go():
        try:
            return await client.probe(timeout=timeout)
        finally:
            await client.aclose()

    return asyncio.run(go())


CHAT_OK = {
    "model": "mock-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 8, "completion_tokens": 2},
}


# =============================================================== 400 / 404
def test_400_surfaces_openai_error_fields_and_model_hint():
    upstream = {
        "error": {
            "message": "The supported API model names are deepseek-flash, "
                       "deepseek-v4-pro, but you passed deepseek-v-4.1.",
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_request_error",
        }
    }
    mock = httpx.MockTransport(lambda request: httpx.Response(400, json=upstream))
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    with pytest.raises(llm_client.LLMBadOutput) as info:
        run_chat(client)

    exc = info.value
    assert exc.code == "LLM_BAD_OUTPUT"                     # 错误码契约不变
    assert exc.status_code == 400
    assert "deepseek-v-4.1" in exc.message                  # 上游原因进了 message
    assert "HTTP 400" in exc.message
    assert exc.detail["status_code"] == 400
    assert exc.detail["upstream"]["code"] == "invalid_request_error"
    assert exc.detail["upstream"]["type"] == "invalid_request_error"
    assert "模型名" in exc.hint                              # 400 → 查模型名/参数


def test_404_points_at_base_url_and_keeps_plaintext_body():
    mock = httpx.MockTransport(
        lambda request: httpx.Response(404, text="404 page not found")
    )
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    with pytest.raises(llm_client.LLMBadOutput) as info:
        run_chat(client)

    exc = info.value
    assert exc.status_code == 404
    assert "404 page not found" in exc.message
    assert "base_url" in exc.hint and "/v1" in exc.hint
    assert exc.detail["body"] == "404 page not found"


# =============================================================== 401 / 403 / 429 / 5xx
def test_401_and_403_stay_unauthorized_with_key_hint():
    for status in (401, 403):
        mock = httpx.MockTransport(lambda request, status=status: httpx.Response(
            status,
            json={"error": {"message": "Authentication Fails, Your api key is invalid",
                            "type": "authentication_error", "code": "invalid_api_key"}},
        ))
        client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

        with pytest.raises(llm_client.LLMUnauthorized) as info:
            run_chat(client)

        exc = info.value
        assert exc.code == "LLM_401"                        # 类型与码都不得改
        assert exc.status_code == status
        assert "Authentication Fails" in exc.message
        assert "密钥" in exc.hint
        assert "invalid_api_key" in json.dumps(exc.detail, ensure_ascii=False)


def test_429_keeps_retry_contract_and_surfaces_reason():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "0"},
            json={"error": {"message": "Rate limit reached for model", "type": "rate_limit"}},
        )

    client = LLMClient(
        CONFIG,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(llm_client.LLMRateLimited) as info:
        run_chat(client)

    exc = info.value
    assert calls["n"] == llm_client.RATE_LIMIT_RETRIES + 1   # 仍是重试 2 次
    assert exc.code == "LLM_429"
    assert exc.retry_after == 0.0
    assert "Rate limit reached" in exc.message
    assert "HTTP 429" in exc.message


def test_5xx_stays_unavailable_with_upstream_reason():
    mock = httpx.MockTransport(
        lambda request: httpx.Response(503, text="upstream overloaded")
    )
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    with pytest.raises(llm_client.LLMUnavailable) as info:
        run_chat(client)

    exc = info.value
    assert exc.code == "LLM_UNAVAILABLE"
    assert exc.status_code == 503
    assert "upstream overloaded" in exc.message
    assert exc.hint == llm_client.LLM_MESSAGES["LLM_UNAVAILABLE"][1]


# =============================================================== 非 JSON / 超长
def test_non_json_body_is_still_readable():
    html = "<html><body><h1>400 Bad Request</h1><p>model not found</p></body></html>"
    mock = httpx.MockTransport(lambda request: httpx.Response(400, text=html))
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    with pytest.raises(llm_client.LLMBadOutput) as info:
        run_chat(client)

    exc = info.value
    assert "400 Bad Request" in exc.message
    assert exc.detail["body"] == html


def test_overlong_body_is_truncated_in_detail_and_message():
    huge = "x" * 5000
    mock = httpx.MockTransport(lambda request: httpx.Response(400, text=huge))
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    with pytest.raises(llm_client.LLMBadOutput) as info:
        run_chat(client)

    exc = info.value
    assert len(exc.detail["body"]) <= llm_client.UPSTREAM_BODY_LIMIT + 1
    assert exc.detail["body"].endswith("…")
    # message 只带前 300 字原因，不把整段响应体灌进 UI
    assert len(exc.message) < len(huge)
    assert exc.message.endswith("…")


# =============================================================== 探针
def test_probe_uses_reasoning_friendly_token_budget():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=CHAT_OK)

    client = LLMClient(
        CONFIG,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = run_probe(client)

    assert result.ok is True
    assert result.error_code is None
    assert result.model == "mock-model"
    assert requests[0].url.path.endswith("/chat/completions")
    payload = json.loads(requests[0].content)
    assert payload["max_tokens"] == llm_client.PROBE_MAX_TOKENS > 8
    assert payload["messages"] == [{"role": "user", "content": "ping"}]


def test_upstream_body_with_key_is_redacted():
    """上游偶尔回显密钥；进 message/detail 前必须脱敏（§13.1）。"""
    mock = httpx.MockTransport(lambda request: httpx.Response(
        401,
        json={"error": {"message": "Incorrect API key provided: sk-test-0000",
                        "type": "authentication_error"}},
    ))
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    with pytest.raises(llm_client.LLMUnauthorized) as info:
        run_chat(client)

    exc = info.value
    assert "sk-test-0000" not in exc.message
    assert "sk-test-0000" not in json.dumps(exc.detail, ensure_ascii=False)
    assert "sk-****" in exc.message


def test_probe_returns_readable_reason_and_hint_on_400():
    upstream = {"error": {"message": "Model Not Exist", "type": "invalid_request_error"}}
    mock = httpx.MockTransport(lambda request: httpx.Response(400, json=upstream))
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    result = run_probe(client)

    assert result.ok is False
    assert result.error_code == "LLM_BAD_OUTPUT"
    assert "Model Not Exist" in result.message
    assert "模型名" in result.message                     # settings/test 没有 hint 字段
    assert "sk-test-0000" not in result.message           # 不回显密钥


def test_probe_401_reports_key_hint():
    mock = httpx.MockTransport(lambda request: httpx.Response(
        401, json={"error": {"message": "invalid api key"}}
    ))
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    result = run_probe(client)

    assert result.ok is False
    assert result.error_code == "LLM_401"
    assert "invalid api key" in result.message
    assert "密钥" in result.message


def test_probe_reports_empty_content_from_reasoning_model():
    """200 但正文为空（预算被 reasoning 吃掉）也要给可操作原因。"""
    mock = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "model": "mock-model",
        "choices": [{"index": 0, "message": {
            "role": "assistant", "content": "", "reasoning_content": "ping..."},
            "finish_reason": "length"}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 128},
    }))
    client = LLMClient(CONFIG, client=httpx.AsyncClient(transport=mock))

    result = run_probe(client)

    assert result.ok is False
    assert result.error_code == "LLM_BAD_OUTPUT"
    assert "没有返回正文" in result.message
    assert "max_tokens" in result.message


def test_probe_never_leaks_key_and_never_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns boom")

    client = LLMClient(
        CONFIG,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = run_probe(client)

    assert result.ok is False
    assert result.error_code == "LLM_UNAVAILABLE"
    assert "sk-test-0000" not in result.message
