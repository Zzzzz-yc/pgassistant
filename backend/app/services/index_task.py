"""索引重建任务（`API.md` §3.22 / §3.23、`MODULES.md` §2.5）。

**为什么是后台线程**：`indexer.index_all` 是同步的、会跑好几秒；直接放进 async 处理函数
会卡住事件循环（`/api/index/status` 也轮询不到）。这里用 `threading.Thread(daemon=True)`
把它挪出事件循环，进度存**内存**（进程重启即丢，`index_state` 表才是持久事实）。

**限流**：`1 次 / 5 分钟`（§7.10 的成本控制口径），由本模块的 `RateLimited` 表达，
API 层映射成 429 `RATE_LIMITED` + `Retry-After`；已有任务在跑时优先返回 409 `INDEX_BUILDING`。

**状态值**：`IndexRunState` 是封闭集合 `idle | running | failed`（schemas/models.py），
**没有 `done`**；成功结束就是 `state="idle"` + `percent=100` + `files_done=files_total`。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core import config as core_config
from ..db.connection import connect
from ..schemas.models import IndexStatus
from . import embeddings, indexer, llm_client

log = logging.getLogger("pgassistant.index")

#: `POST /api/index/rebuild` 的频率上限：1 次 / 5 分钟（API.md §3.22）
RATE_LIMIT_SECONDS = 300.0
#: 成功结束后的状态（`IndexRunState` 没有 done）
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_FAILED = "failed"

_LOCK = threading.Lock()
_STATE: dict = {"task": None, "last_started": 0.0}


class TaskNotFound(Exception):
    """`task_id` 不存在 → 404 `TASK_NOT_FOUND`。"""


class IndexAlreadyRunning(Exception):
    """已有重建任务在跑 → 409 `INDEX_BUILDING`（带 task_id / percent）。"""

    def __init__(self, task_id: str, percent: float = 0.0) -> None:
        self.task_id = task_id
        self.percent = float(percent or 0.0)
        super().__init__("正在建立索引")


class RateLimited(Exception):
    """本地令牌桶/频率限制 → 429 `RATE_LIMITED`（带 retry_after 秒）。"""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, float(retry_after or 0.0))
        super().__init__("重建索引过于频繁")


# ------------------------------------------------------------------ 工具
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_task_id(prefix: str = "idx") -> str:
    return "%s-%s-%s" % (
        prefix,
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"),
        uuid.uuid4().hex[:4],
    )


def _db_path(db_path=None) -> Path:
    return Path(db_path) if db_path is not None else Path(core_config.SETTINGS.db_path)


def _content_dir(content_dir=None) -> Path:
    return Path(content_dir) if content_dir is not None else Path(core_config.SETTINGS.content_dir)


def _count_files(content_dir: Path) -> int:
    try:
        return sum(1 for _ in indexer.iter_markdown(content_dir))
    except OSError:
        return 0


def _db_info(db_path: Path) -> tuple[str | None, str | None]:
    """`(content_version, last_indexed_at)`：没有库 / 没有行就返回 (None, None)。"""
    if not db_path.exists():
        return None, None
    try:
        conn = connect(db_path)
    except Exception:
        return None, None
    try:
        row = conn.execute(
            "SELECT content_version, last_indexed_at FROM index_state WHERE id = 1"
        ).fetchone()
        if not row:
            return None, None
        return row["content_version"], row["last_indexed_at"]
    except Exception:
        return None, None
    finally:
        conn.close()


def _update(task_id: str, **changes) -> None:
    with _LOCK:
        task = _STATE.get("task")
        if not task or task["task_id"] != task_id:
            return
        task.update(changes)
        task["updated_at"] = _now_iso()


# ------------------------------------------------------------------ 重建
def start_rebuild(mode: str = "full", *, db_path=None, content_dir=None) -> str:
    """受理一次重建，返回 task_id（立刻返回，不等索引跑完）。"""
    db = _db_path(db_path)
    content = _content_dir(content_dir)

    with _LOCK:
        running = _STATE.get("task")
        if running and running.get("state") == STATE_RUNNING:
            raise IndexAlreadyRunning(running["task_id"], running.get("percent", 0.0))

        now = time.monotonic()
        last = float(_STATE.get("last_started") or 0.0)
        if last and now - last < RATE_LIMIT_SECONDS:
            raise RateLimited(RATE_LIMIT_SECONDS - (now - last))
        _STATE["last_started"] = now

        ts = _now_iso()
        task_id = new_task_id("idx")
        _STATE["task"] = {
            "task_id": task_id,
            "mode": mode if mode in ("full", "incremental") else "full",
            "state": STATE_RUNNING,
            "db_path": str(db),
            "files_total": _count_files(content),
            "files_done": 0,
            "percent": 0.0,
            "started_at": ts,
            "updated_at": ts,
            "error": None,
        }

    thread = threading.Thread(
        target=_run, args=(task_id, db, content, mode != "incremental"),
        name="pgassistant-index-%s" % task_id, daemon=True,
    )
    thread.start()
    log.info("event=index.start task_id=%s mode=%s", task_id, mode)
    return task_id


def _run(task_id: str, db_path: Path, content_dir: Path, rebuild: bool) -> None:
    try:
        result = indexer.index_all(db_path, content_dir, rebuild=rebuild)
        done = int(result.get("files") or 0)
        with _LOCK:
            task = _STATE.get("task") or {}
            total = max(done, int(task.get("files_total") or 0))
        _update(task_id, state=STATE_IDLE, files_done=done, files_total=total,
                percent=100.0, error=None)
        log.info("event=index.done task_id=%s files=%d skipped=%s",
                 task_id, done, result.get("skipped"))
    except Exception as exc:                       # 线程里的异常必须自己收住
        message = str(exc)[:400]
        _update(task_id, state=STATE_FAILED, error=message)
        log.warning("event=index.failed task_id=%s error=%s", task_id, type(exc).__name__)


# ------------------------------------------------------------------ 向量重建
def start_embedding_rebuild(db_path=None, *, batch: int = embeddings.BATCH_SIZE) -> str | None:
    """设置保存后异步重建向量（本批选定的入口）；未配置 embedding 返回 None。"""
    db = _db_path(db_path)
    config = ""
    try:
        conn = connect(db)
    except Exception:
        return None
    try:
        config = llm_client.load_embedding_config(conn)
    finally:
        conn.close()
    if config is None:
        return None

    task_id = new_task_id("emb")
    threading.Thread(
        target=_run_embeddings, args=(db, config, batch),
        name="pgassistant-embeddings-%s" % task_id, daemon=True,
    ).start()
    log.info("event=embeddings.start task_id=%s model=%s", task_id, config.model)
    return task_id


def _run_embeddings(db_path: Path, config, batch: int) -> None:
    async def _go() -> int:
        client = llm_client.LLMClient(config)
        try:
            conn = connect(db_path)
            try:
                return await embeddings.build_all(conn, client, batch=batch)
            finally:
                conn.close()
        finally:
            try:
                await client.aclose()
            except Exception:
                pass

    try:
        written = asyncio.run(_go())
        log.info("event=embeddings.done written=%d", written)
    except Exception as exc:                       # 上游不可用只影响语义检索
        log.warning("event=embeddings.failed error=%s", type(exc).__name__)


# ------------------------------------------------------------------ 状态
def status(task_id: str | None = None, *, db_path=None) -> IndexStatus:
    """当前 / 指定任务的进度；`task_id` 省略时返回"最近一次"任务。"""
    with _LOCK:
        task = _STATE.get("task")

    if task_id:
        if not task or task["task_id"] != task_id:
            raise TaskNotFound(task_id)

    content_version, last_indexed_at = _db_info(_db_path(db_path))
    if not task:
        return IndexStatus(
            state=STATE_IDLE,
            mode=None,
            task_id=None,
            files_total=0,
            files_done=0,
            percent=0.0,
            started_at=None,
            updated_at=None,
            last_indexed_at=last_indexed_at,
            content_version=content_version,
            error=None,
        )

    return IndexStatus(
        state=task.get("state") or STATE_IDLE,
        mode=task.get("mode"),
        task_id=task.get("task_id"),
        files_total=int(task.get("files_total") or 0),
        files_done=int(task.get("files_done") or 0),
        percent=float(task.get("percent") or 0.0),
        started_at=task.get("started_at"),
        updated_at=task.get("updated_at"),
        last_indexed_at=last_indexed_at,
        content_version=content_version,
        error=task.get("error"),
    )


def reset_limits() -> None:
    """测试用：清掉"上次开始时间"与已结束的任务（不对外暴露成端点）。"""
    with _LOCK:
        _STATE["last_started"] = 0.0
        task = _STATE.get("task")
        if task and task.get("state") != STATE_RUNNING:
            _STATE["task"] = None


def wait_until_idle(timeout: float = 30.0, *, interval: float = 0.01) -> bool:
    """测试用：等后台线程跑完（生产路径靠轮询 `/api/index/status`）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _LOCK:
            task = _STATE.get("task")
        if not task or task.get("state") != STATE_RUNNING:
            return True
        time.sleep(interval)
    return False
