"""SSE 帧与流（M4）。契约：`docs/contracts/API.md` §4（DECISIONS D5）、MODULES.md §2.4。

帧的字节形状（唯一口径，§4.1）：

    event: <name>\\n
    data: <单行 JSON>\\n
    \\n

- 事件名是封闭集合 `intent` / `delta` / `done` / `error`（§4.2）；
- `data` 用 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`，
  字符串里的换行被 JSON 转义成 `\\n`，不会破坏帧边界；
- 注释行只有 `: ping`（§4.2 第 4 条），前端忽略以 `:` 开头的行。

`stream()` 额外承担两件事：

1. **空闲保活**：`ping_seconds` 内没有事件就发一次 `: ping`（反代/浏览器空闲超时）；
2. **断连收尾**：客户端断开时 Starlette 取消协程 → `asyncio.CancelledError`，
   这里转成 `SSEDisconnected` 并**关闭上游生成器**（§7.9：立即中断上游，别白烧钱）。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from ..schemas.models import (
    SseDeltaData,
    SseDoneData,
    SseErrorData,
    SseIntentData,
)

#: 模型类型 → 事件名（§4.2 的封闭集合）。用类型判定而不是让调用方手写事件名，
#: 避免「模型写错事件名」这类只有前端才会发现的错。
EVENT_BY_MODEL: tuple[tuple[type[BaseModel], str], ...] = (
    (SseIntentData, "intent"),
    (SseDeltaData, "delta"),
    (SseDoneData, "done"),
    (SseErrorData, "error"),
)

#: 允许的事件名（拼错就是代码 bug，直接 ValueError）。
EVENT_NAMES = ("intent", "delta", "done", "error")

#: 保活注释行。
PING_FRAME = b": ping\n\n"


class SSEDisconnected(Exception):
    """客户端断连。调用方应立刻 `aclose` 上游并记 `aborted=true`（只进日志，不写库）。"""


def _payload(data: Any) -> Any:
    """把帧数据变成可 JSON 序列化的对象。"""
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
        if isinstance(data, SseErrorData):
            # 与 §4.6 的示例同形：错误帧不塞一堆 null（code/message/hint 为主）
            payload = {key: value for key, value in payload.items() if value is not None}
        return payload
    return data


def frame(event: str, data: BaseModel | dict) -> bytes:
    """一帧 SSE 字节。`event` 必须是四个事件名之一。"""
    if event not in EVENT_NAMES:
        raise ValueError("unknown SSE event: %r" % (event,))
    body = json.dumps(_payload(data), ensure_ascii=False, separators=(",", ":"))
    return ("event: %s\ndata: %s\n\n" % (event, body)).encode("utf-8")


def event_name(model: BaseModel) -> str:
    """按模型类型取事件名；不认识就报错（宁可 500 也不发一个前端忽略的帧）。"""
    for cls, name in EVENT_BY_MODEL:
        if isinstance(model, cls):
            return name
    raise TypeError("unsupported SSE payload: %s" % type(model).__name__)


async def stream(events: AsyncIterator[BaseModel], *,
                 ping_seconds: float = 15.0) -> AsyncIterator[bytes]:
    """把模型流编码成 SSE 字节流，并负责保活与断连收尾。

    `ping_seconds <= 0` 关闭保活（测试用）。
    """
    queue: asyncio.Queue = asyncio.Queue()
    finished = object()

    async def pump() -> None:
        try:
            async for item in events:
                queue.put_nowait(item)
        except BaseException as exc:        # 上游异常原样交给消费者抛出
            queue.put_nowait(exc)
        finally:
            queue.put_nowait(finished)

    task = asyncio.create_task(pump())
    try:
        while True:
            if ping_seconds and ping_seconds > 0:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=ping_seconds)
                except asyncio.TimeoutError:
                    yield PING_FRAME
                    continue
            else:
                item = await queue.get()

            if item is finished:
                break
            if isinstance(item, BaseException):
                if isinstance(item, asyncio.CancelledError):
                    raise SSEDisconnected("client disconnected")
                raise item
            yield frame(event_name(item), item)
    except asyncio.CancelledError as exc:   # 客户端断连 / 服务被关闭
        raise SSEDisconnected("client disconnected") from exc
    finally:
        # 先取消并**等**抽水泵退出：它在 __anext__ 上被取消会把生成器的 finally 跑完，
        # 之后 aclose() 就是幂等的。反过来先 aclose 会撞 "generator already running"。
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:                   # 上游自身的异常已经交给消费者了
            pass
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:               # 收尾失败不覆盖真正的错误
                pass
