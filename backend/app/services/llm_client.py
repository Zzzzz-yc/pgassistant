"""LLM 客户端（M4）。契约：`docs/contracts/MODULES.md` §2.4 / §4.3、API.md §5。

三条不变量（§7.10，验收会查）：

1. **全局并发 ≤2**：模块级 `asyncio.Semaphore(2)`，所有出网调用都从它下面走；
2. **429 指数退避重试 2 次**；超时（默认 60s）**不重试**；401/403 不重试；
   网络错误（`httpx.TransportError`）额外重试 1 次（§7.9「网络错误 → 重试 1 次」）；
3. **日志只记** `status` / `duration_ms` / `tokens_in` / `tokens_out` / `model`，
   绝不记 prompt、对话内容与 api_key（§13.1-3）。

配置加载（`load_llm_config`）与设置写入（M5 的 `settings_service`）**分家**：
本文件只读，从不写。读取顺序（前者优先）：

    base_url / model / temperature / max_tokens
        `settings` 表键 ``llm.base_url`` / ``llm.model`` / ``llm.temperature`` / ``llm.max_tokens``
        → ``data/config.json`` 的 ``llm`` 对象
        → 环境变量 ``PG_LLM_BASE_URL`` / ``PG_LLM_MODEL`` / ``PG_LLM_TEMPERATURE`` / ``PG_LLM_MAX_TOKENS``
    api_key
        环境变量 ``PG_LLM_API_KEY`` → ``data/llm_key``（单行 0600 文件）
        密钥**不进 `settings` 表、不进 `config.json`**（DECISIONS D11）。

M5 落地后由 `services/settings_service.py::llm_config(conn)` 接管：本文件的
`load_llm_config` 先尝试委托它（可选导入），失败再走上面的兜底，所以「M5 未就绪」
与「M5 已就绪」两种状态都不需要改这里。没有任何配置时 `configured=False`，
调用抛 `LLMNotConfigured`——**不抛 ImportError、不崩进程**（§6.6 / §4.8）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from ..core import config as core_config

log = logging.getLogger("pgassistant.llm")

# --------------------------------------------------------------- 常量
#: 429 的额外重试次数（契约 §2.4：「指数退避重试 2 次」）。
RATE_LIMIT_RETRIES = 2
#: 429 的退避序列（秒）。读得到 `Retry-After` 时用上游值，最多夹到 `MAX_BACKOFF`。
RATE_LIMIT_BACKOFF = (0.5, 1.0)
#: 网络错误的额外重试次数（§7.9 只允许 1 次）。
NETWORK_RETRIES = 1
NETWORK_BACKOFF = 0.3
MAX_BACKOFF = 5.0
#: 默认超时（§7.9：60s，且**不自动重试**）。
DEFAULT_TIMEOUT = 60.0

#: 全局并发闸门。`asyncio.Semaphore` 在 3.10+ 不绑定 loop，可在导入期创建。
_SEMAPHORE = asyncio.Semaphore(2)

#: `settings` 表里的非密钥键（写进模块 report 的 CONTRACT_IMPACT，由 M5 固化）。
SETTINGS_KEYS = {
    "base_url": "llm.base_url",
    "model": "llm.model",
    "temperature": "llm.temperature",
    "max_tokens": "llm.max_tokens",
}

#: 环境变量兜底名（M5 固化进 MODULES §1.3 后以文档为准）。
ENV_KEYS = {
    "base_url": "PG_LLM_BASE_URL",
    "model": "PG_LLM_MODEL",
    "temperature": "PG_LLM_TEMPERATURE",
    "max_tokens": "PG_LLM_MAX_TOKENS",
    "api_key": "PG_LLM_API_KEY",
}

#: 密钥文件（相对 `data/`）。M5 换成 keyring 后本兜底自然失效。
SECRET_FILE = "llm_key"

#: 错误码 → (message, hint)。API 层与 `probe()` 共用，避免两处文案漂移。
LLM_MESSAGES: dict[str, tuple[str, str]] = {
    "LLM_NOT_CONFIGURED": (
        "未配置模型",
        "到设置页填写 base_url / model 与 api_key；不配也能手动创建计划",
    ),
    "LLM_401": ("密钥无效或已过期", "到设置页更新 api_key"),
    "LLM_429": ("请求过于频繁", "稍等几秒再试"),
    "LLM_TIMEOUT": ("模型响应超时", "可重试一次；超时不会自动重试以避免费用翻倍"),
    "LLM_UNAVAILABLE": ("模型服务不可用", "检查代理 / 网络后重试"),
    "LLM_BAD_OUTPUT": ("模型输出无法解析", "换一种描述重试"),
}


# --------------------------------------------------------------- 异常
class LLMError(Exception):
    """LLM 调用失败基类。`code` 与 API.md §5 的错误码逐字一致。"""

    code = "LLM_UNAVAILABLE"

    def __init__(self, message: str | None = None, *, status_code: int | None = None,
                 detail: Any | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.message = message or LLM_MESSAGES.get(self.code, ("模型调用失败", ""))[0]
        super().__init__(self.message)

    @property
    def hint(self) -> str:
        return LLM_MESSAGES.get(self.code, ("", ""))[1]


class LLMNotConfigured(LLMError):
    code = "LLM_NOT_CONFIGURED"


class LLMUnauthorized(LLMError):
    code = "LLM_401"


class LLMRateLimited(LLMError):
    code = "LLM_429"

    def __init__(self, message: str | None = None, *, retry_after: float = 0.0,
                 status_code: int | None = 429, detail: Any | None = None) -> None:
        self.retry_after = float(retry_after or 0.0)
        super().__init__(message, status_code=status_code, detail=detail)


class LLMTimeout(LLMError):
    code = "LLM_TIMEOUT"


class LLMUnavailable(LLMError):
    code = "LLM_UNAVAILABLE"


class LLMBadOutput(LLMError):
    code = "LLM_BAD_OUTPUT"


class LLMFormatUnsupported(LLMBadOutput):
    """上游不接受 `response_format`（多数是 400）。

    是 `LLMBadOutput` 的子类，所以映射表不用改（仍是 502 `LLM_BAD_OUTPUT`）；
    `router/classify.py` 靠它区分「格式不支持 → 降级为纯提示词」与「输出不是 JSON → 重试」。
    """

    code = "LLM_BAD_OUTPUT"


# --------------------------------------------------------------- 数据类
@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.3
    max_tokens: int = 1024


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    #: 上游没返回 `usage` 时为 True（`done.usage.estimated` 直接用它）。
    #: 契约 §2.4 只列了前四个字段；这里**追加一个带默认值**的字段，位置参数顺序不变。
    estimated: bool = False


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    latency_ms: int
    model: str | None
    message: str
    error_code: str | None


# --------------------------------------------------------------- 配置加载
def _read_settings_table(conn) -> dict[str, str]:
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'llm.%'"
        ).fetchall()
    except Exception:                       # 库不存在 / 表不存在：当作没配
        return {}
    out: dict[str, str] = {}
    for row in rows:
        try:
            out[row["key"]] = row["value"]
        except (IndexError, TypeError):
            out[row[0]] = row[1]
    return out


def _data_dir():
    return core_config.SETTINGS.data_dir


def _read_config_json() -> dict[str, Any]:
    try:
        path = _data_dir() / "config.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    llm = data.get("llm") if isinstance(data, dict) else None
    return llm if isinstance(llm, dict) else {}


def _read_secret_file() -> str:
    try:
        path = _data_dir() / SECRET_FILE
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _build_config(base_url: str, model: str, api_key: str,
                  temperature, max_tokens) -> LLMConfig | None:
    base_url = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()
    api_key = (api_key or "").strip()
    if not (base_url and model and api_key):
        return None
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        # 0–2（API.md §3.20 的约束），越界按默认值处理而不是报错
        temperature=min(2.0, max(0.0, _as_float(temperature, 0.3))),
        max_tokens=min(8192, max(256, _as_int(max_tokens, 1024))),
    )


def load_llm_config(conn) -> LLMConfig | None:
    """读当前配置；没配齐 `base_url` / `model` / `api_key` 时返回 `None`。

    只读，不写库、不建文件。`conn` 允许为 `None`（`/api/health` 在库不存在时也要能问）。
    """
    # M5 就绪后由 settings_service 接管（可选导入，缺模块不报错）
    try:
        from . import settings_service  # type: ignore[attr-defined]
    except Exception:
        settings_service = None
    if settings_service is not None:
        try:
            config = settings_service.llm_config(conn)
        except Exception as exc:            # 配置读取失败不能连带助手不可用
            log.warning("settings_service.llm_config 失败，回退内置读取: %s", type(exc).__name__)
            config = None
        if config is not None:
            return config

    table = _read_settings_table(conn)
    file_config = _read_config_json()

    def pick(name: str, default=""):
        key = SETTINGS_KEYS[name]
        for source in (table.get(key), file_config.get(name), os.environ.get(ENV_KEYS[name])):
            if source not in (None, ""):
                return source
        return default

    api_key = os.environ.get(ENV_KEYS["api_key"], "").strip() or _read_secret_file()
    return _build_config(
        pick("base_url"),
        pick("model"),
        api_key,
        pick("temperature", 0.3),
        pick("max_tokens", 1024),
    )


def load_embedding_config(conn) -> LLMConfig | None:
    """embedding 的配置（M5 的 `settings_service.embedding_config` 接管后自然让位）。

    M4 只用来回答 `health.embedding_configured`；助手本身**不依赖**它（§4.8）。
    """
    try:
        from . import settings_service  # type: ignore[attr-defined]
    except Exception:
        settings_service = None
    if settings_service is not None:
        try:
            config = settings_service.embedding_config(conn)
        except Exception:
            config = None
        if config is not None:
            return config

    table = _read_settings_table(conn)
    file_config = _read_config_json()
    emb = file_config.get("embedding") if isinstance(file_config.get("embedding"), dict) else {}

    def pick(name: str, default: str = "") -> str:
        key = "embedding.%s" % name
        for source in (table.get(key), emb.get(name), os.environ.get("PG_EMBEDDING_%s" % name.upper())):
            if source not in (None, ""):
                return str(source)
        return default

    return _build_config(pick("base_url"), pick("model"),
                         pick("api_key") or _read_secret_file(), 0.0, 1024)


# --------------------------------------------------------------- 工具
def estimate_tokens(text: str) -> int:
    """粗估 token（中文按 ~1.5 字/token、英文按 ~4 字符/token 的中间值取 3）。"""
    return max(1, round(len(text or "") / 3))


def _retry_after(response: httpx.Response, fallback: float) -> float:
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return min(MAX_BACKOFF, max(0.0, float(raw)))
        except ValueError:
            pass
    return min(MAX_BACKOFF, max(0.0, fallback))


def strip_fences(text: str) -> str:
    """剥掉 ```json … ``` 围栏（§7.5：不支持 json_object 时的降级解析）。"""
    body = (text or "").strip()
    if not body.startswith("```"):
        return body
    body = body[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    end = body.rfind("```")
    if end >= 0:
        body = body[:end]
    return body.strip()


# --------------------------------------------------------------- 客户端
class LLMClient:
    """OpenAI 兼容的 `/chat/completions` 与 `/embeddings`。

    `client` 参数只给测试注入 `httpx.AsyncClient`（或 MockTransport）用；
    不传时自己建一个，`aclose()` 也只关自己建的那个。
    """

    def __init__(self, config: LLMConfig | None,
                 *, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client if client is not None else httpx.AsyncClient()
        self._owns_client = client is None

    # ------------------------------------------------------------- 属性
    @property
    def config(self) -> LLMConfig | None:
        return self._config

    @property
    def configured(self) -> bool:
        return self._config is not None

    def _require(self) -> LLMConfig:
        if self._config is None:
            raise LLMNotConfigured()
        return self._config

    def _headers(self, config: LLMConfig) -> dict[str, str]:
        return {
            "Authorization": "Bearer %s" % config.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------- 出网
    async def _send(self, path: str, payload: dict, *, timeout: float,
                    stream: bool = False, response_format: bool = False) -> httpx.Response:
        """发一次请求；429 / 网络错误按契约重试，其余直接映射成异常。

        调用方负责 `_SEMAPHORE`（这里不获取，避免嵌套死锁）。
        """
        config = self._require()
        url = config.base_url.rstrip("/") + path
        headers = self._headers(config)
        rate_limited = 0
        network_retries = 0
        while True:
            started = time.perf_counter()
            try:
                request = self._client.build_request(
                    "POST", url, json=payload, headers=headers, timeout=timeout
                )
                response = await self._client.send(request, stream=stream)
            except httpx.TimeoutException as exc:      # 超时不重试（§7.9）
                self._log_error("LLM_TIMEOUT", started, None)
                raise LLMTimeout(detail=str(exc)) from exc
            except httpx.TransportError as exc:
                if network_retries < NETWORK_RETRIES:
                    network_retries += 1
                    await asyncio.sleep(NETWORK_BACKOFF)
                    continue
                self._log_error("LLM_UNAVAILABLE", started, None)
                raise LLMUnavailable(detail=str(exc)) from exc

            status = response.status_code
            if status == 429:
                wait = _retry_after(response, RATE_LIMIT_BACKOFF[
                    min(rate_limited, len(RATE_LIMIT_BACKOFF) - 1)
                ])
                await response.aclose()
                if rate_limited < RATE_LIMIT_RETRIES:
                    rate_limited += 1
                    await asyncio.sleep(wait)
                    continue
                self._log_error("LLM_429", started, status)
                raise LLMRateLimited(retry_after=wait, status_code=status)

            self._log_call(status, started)
            if 200 <= status < 300:
                return response
            await response.aclose()
            if status in (401, 403):
                raise LLMUnauthorized(status_code=status)
            if status >= 500:
                raise LLMUnavailable(status_code=status)
            if response_format:
                raise LLMFormatUnsupported(
                    "上游不接受 response_format", status_code=status
                )
            raise LLMBadOutput(
                "上游拒绝了请求（HTTP %d）" % status, status_code=status
            )

    def _log_call(self, status: int, started: float) -> None:
        # 只记状态与耗时：没有 prompt、没有 key、没有对话内容（§13.1-3）
        log.info("event=llm.call status=%s duration_ms=%d",
                 status, int((time.perf_counter() - started) * 1000))

    def _log_error(self, code: str, started: float, status: int | None) -> None:
        log.warning("event=llm.error code=%s status=%s duration_ms=%d",
                    code, status, int((time.perf_counter() - started) * 1000))

    # ------------------------------------------------------------- 对话
    async def chat(self, messages: list[dict], *, max_tokens: int,
                   temperature: float | None = None,
                   response_format: dict | None = None,
                   timeout: float = DEFAULT_TIMEOUT) -> LLMResponse:
        config = self._require()
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": config.temperature if temperature is None else temperature,
            "stream": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        started = time.perf_counter()
        async with _SEMAPHORE:
            response = await self._send(
                "/chat/completions", payload, timeout=timeout,
                response_format=response_format is not None,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMBadOutput("上游响应不是 JSON") from exc
        finally:
            await response.aclose()

        result = self._parse_chat(data, messages, config)
        log.info("event=llm.chat duration_ms=%d tokens_in=%d tokens_out=%d estimated=%s",
                 int((time.perf_counter() - started) * 1000),
                 result.tokens_in, result.tokens_out, result.estimated)
        return result

    def _parse_chat(self, data: Any, messages: list[dict],
                    config: LLMConfig) -> LLMResponse:
        if not isinstance(data, dict):
            raise LLMBadOutput("上游响应结构不对")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMBadOutput("上游没有返回 choices")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise LLMBadOutput("上游没有返回正文")

        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        estimated = not usage
        if estimated:
            prompt_chars = sum(
                len(str(item.get("content", ""))) for item in messages if isinstance(item, dict)
            )
            tokens_in = max(1, round(prompt_chars / 3)) if prompt_chars else 0
            tokens_out = estimate_tokens(text)
        else:
            tokens_in = _as_int(usage.get("prompt_tokens"), 0)
            tokens_out = _as_int(usage.get("completion_tokens"), 0)
        model = data.get("model") if isinstance(data.get("model"), str) else config.model
        return LLMResponse(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            estimated=estimated,
        )

    # ------------------------------------------------------------- 流式
    async def stream(self, messages: list[dict], *, max_tokens: int,
                     temperature: float | None = None,
                     timeout: float = DEFAULT_TIMEOUT) -> AsyncIterator[str]:
        """逐块产出正文增量（OpenAI 兼容的 `data: {...}` 帧）。

        整个生成过程占一个信号量名额（流式请求的耗时全在收字节上，不能只算建连）。
        `agen.aclose()` 会走到 `finally`，把上游响应关掉（§7.9 客户端断连）。
        """
        config = self._require()
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": config.temperature if temperature is None else temperature,
            "stream": True,
        }
        started = time.perf_counter()
        chars = 0
        async with _SEMAPHORE:
            response = await self._send("/chat/completions", payload, timeout=timeout,
                                        stream=True)
            try:
                async for line in response.aiter_lines():
                    chunk = self._delta_from_line(line)
                    if chunk:
                        chars += len(chunk)
                        yield chunk
            finally:
                await response.aclose()
                log.info("event=llm.stream duration_ms=%d chars=%d tokens_out~%d",
                         int((time.perf_counter() - started) * 1000), chars,
                         estimate_tokens("x" * chars) if chars else 0)

    @staticmethod
    def _delta_from_line(line: str) -> str:
        body = (line or "").strip()
        if not body or body.startswith(":"):
            return ""
        if body.startswith("data:"):
            body = body[5:].strip()
        if not body or body == "[DONE]":
            return ""
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise LLMBadOutput("流式响应不是 JSON") from exc
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta")
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        return content if isinstance(content, str) else ""

    # ------------------------------------------------------------- 探针
    async def probe(self, *, timeout: float = 10.0) -> ProbeResult:
        """连通性探针：失败也是 `ok=False` 的返回值，**不抛异常**（API.md §3.21）。"""
        if not self.configured:
            return ProbeResult(False, 0, None, LLM_MESSAGES["LLM_NOT_CONFIGURED"][0],
                               "LLM_NOT_CONFIGURED")
        started = time.perf_counter()
        try:
            response = await self.chat(
                [{"role": "user", "content": "ping"}], max_tokens=8, timeout=timeout
            )
        except LLMError as exc:
            return ProbeResult(
                False,
                int((time.perf_counter() - started) * 1000),
                None,
                exc.message,
                exc.code,
            )
        return ProbeResult(
            True,
            int((time.perf_counter() - started) * 1000),
            response.model,
            "连接正常",
            None,
        )

    # ------------------------------------------------------------- 向量
    async def embed(self, texts: list[str], *, timeout: float = 30.0) -> list[list[float]]:
        """`/embeddings`（M5 的语义检索用；M4 不调用它，但签名在契约里）。"""
        config = self._require()
        payload = {"model": config.model, "input": list(texts)}
        async with _SEMAPHORE:
            response = await self._send("/embeddings", payload, timeout=timeout)
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMBadOutput("上游响应不是 JSON") from exc
        finally:
            await response.aclose()

        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise LLMBadOutput("上游没有返回等长的向量")
        out: list[list[float]] = []
        for row in rows:
            vec = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(vec, list) or not vec:
                raise LLMBadOutput("向量结构不对")
            out.append([float(value) for value in vec])
        return out

    # ------------------------------------------------------------- 收尾
    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
