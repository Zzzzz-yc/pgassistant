"""tools/prepare_content.py 的源地址与内容状态测试。

只测纯函数：不联网、不写 ``data/``，临时内容都放在 pytest 的 tmp_path 下。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREPARE_PATH = ROOT / "tools" / "prepare_content.py"
LAUNCH_PATH = ROOT / "launch.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def prepare():
    return _load("prepare_content_under_test", PREPARE_PATH)


@pytest.fixture(scope="module")
def launch():
    return _load("launch_under_test", LAUNCH_PATH)


def write_article(root: Path, stack: str, category: str, slug: str) -> None:
    path = root / stack / category / (slug + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nstack: %s\ncategory: %s\n---\n\n正文\n" % (stack, category),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# 源地址：镜像拼接与候选顺序
# --------------------------------------------------------------------------
def test_mirror_keeps_owner_and_repo(prepare):
    assert prepare.apply_mirror(
        "https://github.com/krahets/hello-algo",
        "https://mirror.example/https://github.com",
    ) == "https://mirror.example/https://github.com/krahets/hello-algo"


def test_mirror_trims_trailing_slash(prepare):
    assert prepare.apply_mirror(
        "https://github.com/jwasham/coding-interview-university",
        "https://ghfast.top/https://github.com/",
    ) == "https://ghfast.top/https://github.com/jwasham/coding-interview-university"


def test_mirror_rejects_plain_domain(prepare):
    assert prepare.apply_mirror("https://github.com/krahets/hello-algo", "ghfast.top") is None


def test_env_override_has_no_fallback(prepare, monkeypatch):
    monkeypatch.setenv(prepare.HELLO_ALGO_ENV, "https://127.0.0.1:1/nonexistent.git")
    monkeypatch.setenv(prepare.MIRROR_ENV, "https://mirror.example/https://github.com")
    candidates = prepare.source_candidates(
        prepare.OFFICIAL_HELLO_ALGO_URL, prepare.HELLO_ALGO_ENV)
    assert [url for url, _ in candidates] == ["https://127.0.0.1:1/nonexistent.git"]


def test_mirror_then_official(prepare, monkeypatch):
    monkeypatch.delenv(prepare.HELLO_ALGO_ENV, raising=False)
    monkeypatch.setenv(prepare.MIRROR_ENV, "https://mirror.example/https://github.com")
    candidates = prepare.source_candidates(
        prepare.OFFICIAL_HELLO_ALGO_URL, prepare.HELLO_ALGO_ENV)
    assert [url for url, _ in candidates] == [
        "https://mirror.example/https://github.com/krahets/hello-algo",
        "https://github.com/krahets/hello-algo",
    ]


def test_default_is_official(prepare, monkeypatch):
    monkeypatch.delenv(prepare.HELLO_ALGO_ENV, raising=False)
    monkeypatch.delenv(prepare.MIRROR_ENV, raising=False)
    candidates = prepare.source_candidates(
        prepare.OFFICIAL_JWASHAM_URL, prepare.JWASHAM_ENV)
    assert [url for url, _ in candidates] == [prepare.OFFICIAL_JWASHAM_URL]


# --------------------------------------------------------------------------
# 内容状态
# --------------------------------------------------------------------------
def test_stack_counts_separates_interview(prepare, tmp_path):
    write_article(tmp_path, "cs-basics", "array-linkedlist", "array")
    write_article(tmp_path, "cs-basics", "interview", "first-step")
    write_article(tmp_path, "frontend", "html-css", "html-basics")
    assert prepare.stack_counts(tmp_path) == {
        "cs-basics": 1,
        "interview": 1,
        "frontend": 1,
    }


def test_status_flags_missing_cs_basics(prepare, tmp_path, monkeypatch):
    write_article(tmp_path, "frontend", "html-css", "html-basics")
    monkeypatch.setattr(prepare, "body_markdown_count", lambda *args, **kwargs: 1)
    status = prepare.build_content_status(prepare.stack_counts(tmp_path), {})
    assert status["missing"] == ["cs-basics", "interview", "backend"]
    assert status["stacks"]["cs-basics"] == 0
    assert status["total_articles"] == 1
    path = prepare.write_content_status(status, tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["missing"] == status["missing"]
    assert not (tmp_path / "content-status.json.tmp").exists()


# --------------------------------------------------------------------------
# 启动提示
# --------------------------------------------------------------------------
def test_launch_warns_when_missing(launch, tmp_path, monkeypatch, capsys):
    status_path = tmp_path / "content-status.json"
    status_path.write_text(
        json.dumps({"total_articles": 42, "stacks": {"cs-basics": 0},
                    "missing": ["cs-basics"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(launch, "CONTENT_STATUS", status_path)
    launch.warn_if_missing_content()
    out = capsys.readouterr().out
    assert "算法（cs-basics）" in out
    assert "42 篇" in out
    assert "PG_GIT_MIRROR" in out
    assert "tools/prepare_content.py --force" in out


def test_launch_silent_when_complete(launch, tmp_path, monkeypatch, capsys):
    status_path = tmp_path / "content-status.json"
    status_path.write_text(
        json.dumps({"total_articles": 150, "missing": []}), encoding="utf-8")
    monkeypatch.setattr(launch, "CONTENT_STATUS", status_path)
    launch.warn_if_missing_content()
    assert capsys.readouterr().out == ""
