"""错误信封的最小单测（不起服务）。

只锁 D24 新增的 `MEDIA_NOT_FOUND`：常量存在、且 404 信封能带这个码
（`/media/*` 的完整行为在资料页 M2 的 `api/media.py` 落地后另测，见 API.md §3.26）。
"""
from __future__ import annotations

import json

from app.core import errors


def test_media_not_found_is_a_404_envelope_code():
    assert errors.MEDIA_NOT_FOUND == "MEDIA_NOT_FOUND"

    resp = errors.error_response(
        404,
        errors.MEDIA_NOT_FOUND,
        "媒体文件不存在",
        detail={"path": "frontend/html-css/assets/nope.webp"},
        hint="检查正文里的图片相对路径，或重跑内容校验与索引",
    )
    body = json.loads(resp.body)

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert body["code"] == "MEDIA_NOT_FOUND"
    assert body["message"] == "媒体文件不存在"
    assert body["detail"]["path"].endswith(".webp")
    assert body["hint"]
