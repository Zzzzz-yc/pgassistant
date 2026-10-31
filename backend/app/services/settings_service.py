"""设置读写（`API.md` §3.19–§3.21 / `MODULES.md` §2.5 / DECISIONS D11 + D26）。

**键名（D26 固化，逐键 JSON 文本）**：`llm.base_url` / `llm.model` / `llm.temperature` /
`llm.max_tokens`、`embedding.base_url` / `embedding.model` / `embedding.dim`，加上
`private_dir` / `language` / `code_runner_enabled` / `onboarded` / `last_private_scan`。

**读取优先级**（验收会逐条查）：非密钥 **环境变量 > `settings` 表 > 内置默认值**；
`api_key` **环境变量 > keyring/0600 文件**。环境变量是只读覆盖，`PUT` 不写 env。

**密钥红线**：`api_key` 只写不读，回读恒为掩码（`secrets.mask_secret`）；日志不出现明文。

**写后动作**：清配置缓存（`llm_client` 若有公开失效函数就调，当前它每次现读、没有缓存），
并在保存了 embedding 配置后异步重建向量（本批选定的入口，见交付报告）。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from ..schemas.models import (
    EmbeddingConfigRead,
    LLMConfigRead,
    SettingsRead,
    SettingsTestRequest,
    SettingsTestResponse,
    SettingsWrite,
)
from . import private_scanner, secrets
from .llm_client import DEFAULT_MAX_TOKENS, LLMClient, LLMConfig, LLMError

log = logging.getLogger("pgassistant.settings")

#: `settings` 表里的非密钥键（D26 / MODULES §2.5）
LLM_KEYS = {
    "base_url": "llm.base_url",
    "model": "llm.model",
    "temperature": "llm.temperature",
    "max_tokens": "llm.max_tokens",
}
EMBEDDING_KEYS = {
    "base_url": "embedding.base_url",
    "model": "embedding.model",
    "dim": "embedding.dim",
}
PLAIN_KEYS = ("private_dir", "language", "code_runner_enabled", "onboarded")

#: 环境变量（MODULES §1.3，全部只读覆盖）
ENV_LLM = {
    "base_url": "PG_LLM_BASE_URL",
    "model": "PG_LLM_MODEL",
    "temperature": "PG_LLM_TEMPERATURE",
    "max_tokens": "PG_LLM_MAX_TOKENS",
    "api_key": "PG_LLM_API_KEY",
}
ENV_EMBEDDING = {
    "base_url": "PG_EMBEDDING_BASE_URL",
    "model": "PG_EMBEDDING_MODEL",
    "api_key": "PG_EMBEDDING_API_KEY",
}

DEFAULT_TEMPERATURE = 0.3
# `DEFAULT_MAX_TOKENS` 从 `llm_client` 引入（单一事实来源，C3）；这里不再重复定义。
DEFAULT_LANGUAGE = "zh-CN"
DEFAULT_CODE_RUNNER = True
DEFAULT_ONBOARDED = False

#: 探针超时（§10「保存前发一条 1 token 探针」；超过 10s 就算连不通）
PROBE_TIMEOUT = 10.0


# ------------------------------------------------------------------ 小工具
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _as_int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clamp(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def read_table(conn) -> dict:
    """读整张 `settings` 表：值是 JSON 文本，解析失败按原始字符串处理（M4 手写过裸值）。"""
    if conn is None:
        return {}
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    except sqlite3.Error:
        return {}
    table: dict = {}
    for row in rows:
        key = row["key"] if hasattr(row, "keys") else row[0]
        raw = row["value"] if hasattr(row, "keys") else row[1]
        try:
            table[key] = json.loads(raw)
        except (TypeError, ValueError):
            table[key] = raw
    return table


def _write_row(conn, key: str, value, ts: str) -> None:
    conn.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                          updated_at = excluded.updated_at""",
        (key, json.dumps(value, ensure_ascii=False), ts),
    )


def _effective(table: dict, key: str, env_name: str, default=None):
    """**env > settings 表 > 默认值**（D26）。空字符串视为"没配"。"""
    env_value = _env(env_name)
    if env_value:
        return env_value
    value = table.get(key)
    if value is not None and value != "":
        return value
    return default


def _clean_text(value) -> str:
    return str(value).strip() if value is not None else ""


# ------------------------------------------------------------------ 配置
def llm_config(conn) -> LLMConfig | None:
    """当前 LLM 配置（含明文密钥，只在后端流转）；缺任一项 → None。"""
    table = read_table(conn)
    base_url = _clean_text(_effective(table, LLM_KEYS["base_url"], ENV_LLM["base_url"]))
    model = _clean_text(_effective(table, LLM_KEYS["model"], ENV_LLM["model"]))
    api_key = _env(ENV_LLM["api_key"]) or (secrets.load_secret(secrets.LLM_KEY) or "")
    if not (base_url and model and api_key):
        return None
    return LLMConfig(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        temperature=_clamp(
            _effective(table, LLM_KEYS["temperature"], ENV_LLM["temperature"],
                       DEFAULT_TEMPERATURE),
            0.0, 2.0, DEFAULT_TEMPERATURE,
        ),
        max_tokens=int(_clamp(
            _effective(table, LLM_KEYS["max_tokens"], ENV_LLM["max_tokens"],
                       DEFAULT_MAX_TOKENS),
            256, 8192, DEFAULT_MAX_TOKENS,
        )),
    )


def embedding_config(conn) -> LLMConfig | None:
    """embedding 配置（复用 `LLMConfig` 载体，temperature 无意义）；缺项 → None。"""
    table = read_table(conn)
    base_url = _clean_text(_effective(table, EMBEDDING_KEYS["base_url"],
                                      ENV_EMBEDDING["base_url"]))
    model = _clean_text(_effective(table, EMBEDDING_KEYS["model"], ENV_EMBEDDING["model"]))
    api_key = _env(ENV_EMBEDDING["api_key"]) or (secrets.load_secret(secrets.EMBEDDING_KEY) or "")
    if not (base_url and model and api_key):
        return None
    return LLMConfig(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        temperature=0.0,
        max_tokens=DEFAULT_MAX_TOKENS,
    )


def _stored_dim(table: dict) -> int | None:
    raw = table.get(EMBEDDING_KEYS["dim"])
    if raw in (None, ""):
        return None
    dim = _as_int(raw, 0)
    return dim if 1 <= dim <= 8192 else None


def _stored_language(table: dict) -> str:
    value = table.get("language")
    return value if value in ("zh-CN", "en-US") else DEFAULT_LANGUAGE


def _stored_bool(table: dict, key: str, default: bool) -> bool:
    value = table.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    if value is None:
        return default
    return bool(value)


def _stored_private_dir(table: dict) -> str | None:
    value = table.get("private_dir")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# ------------------------------------------------------------------ 读
def read_settings(conn) -> SettingsRead:
    """`GET /api/settings`：api_key 只出掩码，`secret_backend` 说明存在哪一级。"""
    table = read_table(conn)
    llm_key = _env(ENV_LLM["api_key"]) or (secrets.load_secret(secrets.LLM_KEY) or "")
    embedding_key = _env(ENV_EMBEDDING["api_key"]) or (
        secrets.load_secret(secrets.EMBEDDING_KEY) or ""
    )

    llm = LLMConfigRead(
        base_url=_clean_text(_effective(table, LLM_KEYS["base_url"], ENV_LLM["base_url"])) or None,
        model=_clean_text(_effective(table, LLM_KEYS["model"], ENV_LLM["model"])) or None,
        temperature=_clamp(
            _effective(table, LLM_KEYS["temperature"], ENV_LLM["temperature"],
                       DEFAULT_TEMPERATURE),
            0.0, 2.0, DEFAULT_TEMPERATURE,
        ),
        max_tokens=int(_clamp(
            _effective(table, LLM_KEYS["max_tokens"], ENV_LLM["max_tokens"],
                       DEFAULT_MAX_TOKENS),
            256, 8192, DEFAULT_MAX_TOKENS,
        )),
        api_key_masked=secrets.mask_secret(llm_key),
    )
    embedding = EmbeddingConfigRead(
        base_url=_clean_text(_effective(table, EMBEDDING_KEYS["base_url"],
                                        ENV_EMBEDDING["base_url"])) or None,
        model=_clean_text(_effective(table, EMBEDDING_KEYS["model"],
                                     ENV_EMBEDDING["model"])) or None,
        dim=_stored_dim(table),
        api_key_masked=secrets.mask_secret(embedding_key),
    )
    return SettingsRead(
        llm=llm,
        embedding=embedding,
        private_dir=_stored_private_dir(table),
        language=_stored_language(table),
        code_runner_enabled=_stored_bool(table, "code_runner_enabled", DEFAULT_CODE_RUNNER),
        onboarded=_stored_bool(table, "onboarded", DEFAULT_ONBOARDED),
        secret_backend=secrets.backend_in_use(),
        llm_configured=llm_config(conn) is not None,
        embedding_configured=embedding_config(conn) is not None,
    )


# ------------------------------------------------------------------ 写
def write_settings(conn, payload: SettingsWrite) -> SettingsRead:
    """`PUT /api/settings`：分组可选，未提供的组保持原值（局部更新）。

    校验先于写入（`private_dir` 不合法时一行都不写）；DB 事务提交后才写密钥存储，
    避免"表里改了配置、密钥却没存进去"的半状态。
    """
    fields = payload.model_fields_set
    ts = _now()

    rows: list[tuple[str, object]] = []
    secret_jobs: list[tuple[str, str]] = []

    llm = payload.llm
    if llm is not None:
        rows.append((LLM_KEYS["base_url"], llm.base_url.strip().rstrip("/")))
        rows.append((LLM_KEYS["model"], llm.model.strip()))
        rows.append((LLM_KEYS["temperature"], float(llm.temperature)))
        rows.append((LLM_KEYS["max_tokens"], int(llm.max_tokens)))
        if llm.api_key:                            # 不传 = 保持原值（§2.5）
            secret_jobs.append((secrets.LLM_KEY, llm.api_key.strip()))

    embedding = payload.embedding
    if embedding is not None:
        rows.append((EMBEDDING_KEYS["base_url"], embedding.base_url.strip().rstrip("/")))
        rows.append((EMBEDDING_KEYS["model"], embedding.model.strip()))
        if embedding.dim is not None:
            rows.append((EMBEDDING_KEYS["dim"], int(embedding.dim)))
        if embedding.api_key:
            secret_jobs.append((secrets.EMBEDDING_KEY, embedding.api_key.strip()))

    private_dir: Path | None = None
    if "private_dir" in fields:
        if payload.private_dir is None:
            rows.append(("private_dir", None))
        else:
            # 不存在/不可读 → PrivateDirInvalid(400)；越界 → PathForbidden(403)
            private_dir = private_scanner.resolve_private_root(payload.private_dir)
            rows.append(("private_dir", private_scanner.display_root(private_dir)))

    if payload.language is not None:
        rows.append(("language", payload.language))
    if payload.code_runner_enabled is not None:
        rows.append(("code_runner_enabled", bool(payload.code_runner_enabled)))

    with conn:
        for key, value in rows:
            _write_row(conn, key, value, ts)

    for name, value in secret_jobs:
        secrets.store_secret(name, value)

    _invalidate_config_cache()
    if "embedding" in fields:
        _schedule_embedding_rebuild(conn)

    log.info("event=settings.write keys=%d secrets=%d", len(rows), len(secret_jobs))
    return read_settings(conn)


def _invalidate_config_cache() -> None:
    """清 M4 的配置缓存（若将来加了缓存）。

    `llm_client` 目前**每次现读**、没有进程内缓存，所以这里是空操作；
    保留这个钩子是为了"缓存一旦加进来就自动失效"，不要求后续模块改调用点。
    """
    try:
        from . import llm_client
    except Exception:                              # pragma: no cover - 循环导入兜底
        return
    hook = getattr(llm_client, "invalidate_config_cache", None)
    if callable(hook):
        try:
            hook()
        except Exception:
            log.warning("event=settings.invalidate failed")


def _db_path_of(conn) -> Path | None:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return None
    path = row[2] if row and len(row) > 2 else ""
    return Path(path) if path else None


def _schedule_embedding_rebuild(conn) -> None:
    """保存设置后异步重建向量（本批选定的入口，见交付报告）。

    只重建**配置齐全时**的缺失向量；未配 embedding 就什么都不做（§6.6 降级）。
    """
    if embedding_config(conn) is None:
        return
    db_path = _db_path_of(conn)
    try:
        from . import index_task
        index_task.start_embedding_rebuild(db_path)
    except Exception as exc:                       # 后台任务不能把保存请求打挂
        log.warning("event=settings.embedding_rebuild failed error=%s", type(exc).__name__)


# ------------------------------------------------------------------ 探针
async def test_connection(conn, req: SettingsTestRequest) -> SettingsTestResponse:
    """`POST /api/settings/test`：**探针失败也是 200**（业务结果不是 HTTP 错误）。

    带 override 时用请求里的配置测，不落库（`API.md` §3.21）。
    """
    if req.kind == "llm":
        return await _probe_llm(conn, req.llm)
    return await _probe_embedding(conn, req.embedding)


async def _probe_llm(conn, override) -> SettingsTestResponse:
    if override is not None:
        api_key = (override.api_key or "").strip() or _env(ENV_LLM["api_key"]) or (
            secrets.load_secret(secrets.LLM_KEY) or ""
        )
        config = None
        if override.base_url and override.model and api_key:
            config = LLMConfig(
                base_url=override.base_url.strip().rstrip("/"),
                api_key=api_key,
                model=override.model.strip(),
                temperature=float(override.temperature),
                max_tokens=int(override.max_tokens),
            )
    else:
        config = llm_config(conn)

    if config is None:
        return SettingsTestResponse(
            ok=False, kind="llm", latency_ms=0, model=None,
            message="未配置模型", error_code="LLM_NOT_CONFIGURED",
        )

    client = LLMClient(config)
    try:
        probe = await client.probe(timeout=PROBE_TIMEOUT)
    except LLMError as exc:                        # probe 本身不抛，这里只兜底
        return SettingsTestResponse(
            ok=False, kind="llm", latency_ms=0, model=None,
            message=exc.message, error_code=exc.code,
        )
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    return SettingsTestResponse(
        ok=probe.ok,
        kind="llm",
        latency_ms=probe.latency_ms,
        model=probe.model,
        message=probe.message,
        error_code=probe.error_code,
    )


async def _probe_embedding(conn, override) -> SettingsTestResponse:
    if override is not None:
        api_key = (override.api_key or "").strip() or _env(ENV_EMBEDDING["api_key"]) or (
            secrets.load_secret(secrets.EMBEDDING_KEY) or ""
        )
        config = None
        if override.base_url and override.model and api_key:
            config = LLMConfig(
                base_url=override.base_url.strip().rstrip("/"),
                api_key=api_key,
                model=override.model.strip(),
                temperature=0.0,
                max_tokens=DEFAULT_MAX_TOKENS,
            )
    else:
        config = embedding_config(conn)

    if config is None:
        return SettingsTestResponse(
            ok=False, kind="embedding", latency_ms=0, model=None,
            message="未配置语义检索", error_code="NO_EMBEDDING",
        )

    client = LLMClient(config)
    started = time.perf_counter()
    try:
        vectors = await client.embed(["ping"], timeout=PROBE_TIMEOUT)
    except LLMError as exc:
        return SettingsTestResponse(
            ok=False, kind="embedding",
            latency_ms=max(int((time.perf_counter() - started) * 1000), 0),
            model=None,
            message=exc.message,
            error_code=exc.code,
        )
    except Exception as exc:                       # 结构错误等：不把 500 抛给设置页
        return SettingsTestResponse(
            ok=False, kind="embedding",
            latency_ms=max(int((time.perf_counter() - started) * 1000), 0),
            model=None,
            message="探测失败：%s" % type(exc).__name__,
            error_code="LLM_BAD_OUTPUT",
        )
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    latency = max(int((time.perf_counter() - started) * 1000), 0)
    ok = bool(vectors and vectors[0])

    # `embedding.dim` 空则由首次返回推断（MODULES §2.5）；只填一次，不覆盖用户手填值
    if ok and conn is not None:
        try:
            table = read_table(conn)
            if _stored_dim(table) is None:
                with conn:
                    _write_row(conn, EMBEDDING_KEYS["dim"], len(vectors[0]), _now())
                _invalidate_config_cache()
        except sqlite3.Error:
            pass

    return SettingsTestResponse(
        ok=ok,
        kind="embedding",
        latency_ms=latency,
        model=config.model if ok else None,
        message="连接正常" if ok else "上游没有返回向量",
        error_code=None if ok else "LLM_BAD_OUTPUT",
    )
