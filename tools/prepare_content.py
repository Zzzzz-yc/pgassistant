#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prepare_content.py — 首次运行 / 重建内容库。

用法::

    python tools/prepare_content.py                已有内容时跳过
    python tools/prepare_content.py --force        清掉生成的内容后重建
    python tools/prepare_content.py --print-sources 只打印将尝试的源地址

流程：

1. 源缓存 ``data/.sources/``（已 gitignore）：缺少时浅克隆
   - hello-algo：只取 ``docs/``
   - coding-interview-university：只取拆分脚本需要的文件
2. ``convert_content.py`` 转换 hello-algo 章节
3. ``split_jwasham.py`` 拆分 jwasham 清单
4. ``fetch_intro.py`` 按 ``tools/sources.json`` 抓取外站教程正文

源地址可用环境变量覆盖（只读 ``PG_*`` 环境变量，不读 ``.env`` 文件）：

* ``PG_HELLO_ALGO_URL`` / ``PG_JWASHAM_URL``：直接指定仓库地址；指定后只尝试该地址。
* ``PG_GIT_MIRROR``：GitHub 前缀式镜像，例如
  ``https://ghfast.top/https://github.com``；镜像失败会自动回退官方地址。

每次运行都会把内容完整性写入 ``data/.sources/content-status.json``。
hello-algo 是算法内容（cs-basics）的唯一来源：它没有产出任何文章时，即使其它源
成功，本脚本也以退出码 1 结束，并明确打印「算法资料缺失」。

``content/_meta/`` 是随仓库提供的手写元数据，本脚本不会覆盖或删除它。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
CONTENT = ROOT / "content"
META = CONTENT / "_meta"
DEFAULT_SOURCES = ROOT / "data" / ".sources"
LOG_PATH = DEFAULT_SOURCES / "prepare-content.log"
STATUS_NAME = "content-status.json"

GITHUB_PREFIX = "https://github.com"
OFFICIAL_HELLO_ALGO_URL = GITHUB_PREFIX + "/krahets/hello-algo"
OFFICIAL_JWASHAM_URL = GITHUB_PREFIX + "/jwasham/coding-interview-university"
HELLO_ALGO_ENV = "PG_HELLO_ALGO_URL"
JWASHAM_ENV = "PG_JWASHAM_URL"
MIRROR_ENV = "PG_GIT_MIRROR"

# 状态文件里固定检查的四个大类：cs-basics 算法、interview 面试、frontend 前端、backend 后端
EXPECTED_STACKS = ("cs-basics", "interview", "frontend", "backend")
STACK_LABELS = {"cs-basics": "算法", "interview": "面试", "frontend": "前端", "backend": "后端"}

PYTHON = sys.executable

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True, errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True, errors="replace")
except Exception:  # pragma: no cover - 老解释器 / 特殊流
    pass


class PrepareError(Exception):
    """可预期失败：打印原因，不抛 traceback。"""


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def step(message: str) -> None:
    print("==> " + message, flush=True)


def ok(message: str) -> None:
    print("  ok  " + message, flush=True)


def info(message: str) -> None:
    print("  --  " + message, flush=True)


def warn(message: str) -> None:
    print("  !!  " + message, flush=True)


def body_markdown_count(content: Path = CONTENT) -> int:
    """``content/`` 下真正由脚本生成的正文篇数（不含 _meta 与 CREDITS）。"""
    root = Path(content)
    if not root.is_dir():
        return 0
    count = 0
    for path in root.rglob("*.md"):
        parts = path.relative_to(root).parts
        if parts and parts[0] == "_meta":
            continue
        if path.name.lower().startswith("credits"):
            continue
        count += 1
    return count


def log_append(title: str, text: str) -> None:
    """把子进程输出落到数据目录，终端只留摘要。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n===== %s %s =====\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), title))
            handle.write(text.rstrip() + "\n")
    except OSError:
        pass


def tail(text: str, lines: int = 3) -> str:
    rows = [row for row in (text or "").strip().splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


# --------------------------------------------------------------------------
# 内容源地址：环境变量覆盖 → 镜像 → 官方地址
# --------------------------------------------------------------------------
def env_value(name: str) -> str:
    return os.environ.get(name, "").strip()


def apply_mirror(url: str, mirror: str) -> str | None:
    """把官方 GitHub 地址的 ``https://github.com`` 前缀换成镜像前缀。

    镜像语法（国内常见）：``https://ghfast.top/https://github.com/<owner>/<repo>``。
    """
    prefix = (mirror or "").strip().rstrip("/")
    if not prefix or not prefix.lower().startswith(("http://", "https://")):
        return None
    if not url.startswith(GITHUB_PREFIX + "/"):
        return None
    return prefix + url[len(GITHUB_PREFIX):]


def source_candidates(official: str, env_name: str):
    """按尝试顺序返回 ``[(url, 来源说明)]``。

    * 显式指定 ``env_name`` 时只尝试该地址，避免“看起来配了、实际又回退官方”。
    * 设置 ``PG_GIT_MIRROR`` 时先镜像、失败再回退官方。
    """
    override = env_value(env_name)
    if override:
        return [(override, "环境变量 " + env_name)]
    candidates = []
    mirror = env_value(MIRROR_ENV)
    if mirror:
        mirrored = apply_mirror(official, mirror)
        if mirrored:
            candidates.append((mirrored, MIRROR_ENV))
    candidates.append((official, "官方 GitHub"))
    return candidates


def print_sources() -> int:
    """``--print-sources``：只打印将尝试的地址，不读写任何内容。"""
    print("内容源地址（按尝试顺序）：", flush=True)
    for label, official, env_name in (
        ("hello-algo", OFFICIAL_HELLO_ALGO_URL, HELLO_ALGO_ENV),
        ("jwasham", OFFICIAL_JWASHAM_URL, JWASHAM_ENV),
    ):
        print("  %s:" % label, flush=True)
        for index, (url, origin) in enumerate(source_candidates(official, env_name), 1):
            print("    %d. %s  [%s]" % (index, url, origin), flush=True)
    mirror = env_value(MIRROR_ENV)
    if mirror and not apply_mirror(OFFICIAL_HELLO_ALGO_URL, mirror):
        print("  !!  %s 不是有效的前缀式镜像（需以 http:// 或 https:// 开头）" % MIRROR_ENV,
              flush=True)
    return 0


def clone_with_fallback(official: str, env_name: str, destination: Path, patterns, label: str):
    """按候选顺序尝试克隆；返回 ``(成功的 url, 失败摘要列表)``。"""
    candidates = source_candidates(official, env_name)
    errors = []
    for url, origin in candidates:
        try:
            clone_sparse(url, destination, patterns, label)
        except PrepareError as exc:
            errors.append("%s（%s）：%s" % (origin, url, exc))
            warn("%s：%s 失败 %s" % (label, origin, url))
            continue
        if len(candidates) > 1:
            info("%s：实际使用 %s（%s）" % (label, url, origin))
        return url, errors
    raise PrepareError("候选地址均失败：" + "；".join(errors))


# --------------------------------------------------------------------------
# 内容完整性状态
# --------------------------------------------------------------------------
def read_frontmatter(path: Path) -> dict:
    """只读 front-matter 里的 stack / category，够算大类篇数即可。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in ("stack", "category"):
            fields[key] = value.strip().strip("'\"")
    return fields


def stack_counts(content: Path = CONTENT) -> dict:
    """按大类统计生成的文章数；interview 单独从 cs-basics 里拆出来。"""
    counts = {}
    root = Path(content)
    if not root.is_dir():
        return counts
    for path in root.rglob("*.md"):
        parts = path.relative_to(root).parts
        if parts and parts[0] == "_meta":
            continue
        if path.name.lower().startswith("credits"):
            continue
        fields = read_frontmatter(path)
        stack = fields.get("stack") or (parts[0] if parts else "unknown")
        category = fields.get("category", "")
        key = "interview" if stack == "cs-basics" and category == "interview" else stack
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_content_status(counts: dict, sources: dict) -> dict:
    stacks = {}
    for name in EXPECTED_STACKS:
        stacks[name] = counts.get(name, 0)
    for name in sorted(counts):
        if name not in stacks:
            stacks[name] = counts[name]
    missing = [name for name in EXPECTED_STACKS if stacks.get(name, 0) <= 0]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_articles": body_markdown_count(),
        "stacks": stacks,
        "sources": sources,
        "missing": missing,
    }


def write_content_status(status: dict, sources_dir: Path = DEFAULT_SOURCES) -> Path:
    path = Path(sources_dir) / STATUS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def missing_labels(names) -> str:
    return "、".join("%s（%s）" % (STACK_LABELS.get(name, name), name) for name in names)


def print_missing_algorithms(status: dict) -> None:
    print("", flush=True)
    print("!! 算法资料缺失：hello-algo 没有产出任何文章（cs-basics = 0 篇）。", flush=True)
    print("   当前内容库共 %d 篇；资料库将没有算法大类。" % status.get("total_articles", 0),
          flush=True)
    print("   修复：配置镜像 PG_GIT_MIRROR，或给 git 设置代理后重跑：", flush=True)
    print("         python tools/prepare_content.py --force", flush=True)
    print("   失败明细：%s" % rel(LOG_PATH), flush=True)


def child_env() -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run(command, label: str) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env(),
        )
    except OSError as exc:
        raise PrepareError("无法执行 %s：%s" % (label, exc)) from exc
    log_append(label, (completed.stdout or "") + (completed.stderr or ""))
    return completed


def git_exe() -> str:
    exe = shutil.which("git")
    if not exe:
        raise PrepareError("找不到 git 命令；首次运行需要联网，且需要 git 命令可用。")
    return exe


def clone_sparse(url: str, destination: Path, patterns, label: str) -> None:
    """浅克隆 + 稀疏检出；只保留 patterns 指定的路径。"""
    destination = Path(destination)
    if destination.exists():
        return
    git = git_exe()
    partial = destination.with_name(destination.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    info("浅克隆 %s" % url)
    completed = run(
        [git, "clone", "--depth", "1", "--filter=blob:none", "--sparse", url, str(partial)],
        label + " clone",
    )
    if completed.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        detail = tail((completed.stderr or "") + (completed.stdout or ""), 2)
        raise PrepareError("克隆失败（退出码 %d）%s" % (completed.returncode, ("：" + detail) if detail else ""))

    completed = run(
        [git, "-C", str(partial), "sparse-checkout", "set", "--no-cone", *patterns],
        label + " sparse-checkout",
    )
    if completed.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        detail = tail((completed.stderr or "") + (completed.stdout or ""), 2)
        raise PrepareError("稀疏检出失败（退出码 %d）%s" % (completed.returncode, ("：" + detail) if detail else ""))

    try:
        partial.rename(destination)
    except OSError as exc:
        shutil.rmtree(partial, ignore_errors=True)
        raise PrepareError("源目录落盘失败：%s" % exc) from exc


def clear_generated(content: Path = CONTENT) -> None:
    """删掉脚本生成的内容，保留手写的 ``_meta/``。"""
    if not content.is_dir():
        return
    for child in content.iterdir():
        if child.name == "_meta":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def run_convert() -> str:
    source = (Path(SOURCES_DIR) / "hello-algo" / "docs")
    if not source.is_dir():
        raise PrepareError("缺少 hello-algo 源，跳过转换")
    completed = run(
        [PYTHON, str(TOOLS / "convert_content.py"), "--src", str(Path(SOURCES_DIR)), "--out", str(CONTENT)],
        "convert_content.py",
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise PrepareError("convert_content.py 退出码 %d %s" % (completed.returncode, tail(output)))
    match = None
    for line in output.splitlines():
        if line.startswith("转换文件:"):
            match = line
    if not match:
        raise PrepareError("convert_content.py 没有输出转换结果：" + tail(output, 2))
    try:
        count = int(match.split(":", 1)[1].split("|")[0].strip())
    except ValueError:
        count = 0
    if count <= 0:
        raise PrepareError("convert_content.py 没有生成文章")
    return "转换 %d 篇" % count


def run_split() -> str:
    source = Path(SOURCES_DIR) / "jwasham" / "translations" / "README-cn.md"
    if not source.is_file():
        raise PrepareError("缺少 jwasham 源，跳过拆分")
    completed = run(
        [PYTHON, str(TOOLS / "split_jwasham.py"), "--src", str(source), "--out", str(CONTENT)],
        "split_jwasham.py",
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise PrepareError("split_jwasham.py 退出码 %d %s" % (completed.returncode, tail(output)))
    match = None
    for line in output.splitlines():
        if line.startswith("拆分完成："):
            match = line
    if not match:
        raise PrepareError("split_jwasham.py 没有输出拆分结果：" + tail(output, 2))
    try:
        count = int(match.split("：", 1)[1].split(" ", 1)[0].strip())
    except ValueError:
        count = 0
    if count <= 0:
        raise PrepareError("split_jwasham.py 没有生成文章")
    return "拆分 %d 篇" % count


def run_fetch() -> str:
    completed = run(
        [PYTHON, str(TOOLS / "fetch_intro.py"), "--out", str(CONTENT), "--batch", "all"],
        "fetch_intro.py",
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise PrepareError("fetch_intro.py 退出码 %d %s" % (completed.returncode, tail(output)))
    done = None
    for line in output.splitlines():
        if line.startswith("完成:"):
            done = line
    if not done:
        raise PrepareError("fetch_intro.py 没有输完成情况：" + tail(output, 2))
    try:
        succeeded, total = [int(part.strip()) for part in done.split(":", 1)[1].split("/", 1)]
    except ValueError:
        succeeded, total = 0, 0
    if succeeded <= 0:
        raise PrepareError("教程正文抓取全部失败 %s" % tail(output, 2))
    if succeeded < total:
        return "抓取 %d/%d 篇（其余失败，见日志）" % (succeeded, total)
    return "抓取 %d 篇" % succeeded


SOURCES_DIR = DEFAULT_SOURCES


class Stage:
    def __init__(self, name: str):
        self.name = name
        self.status = "pending"
        self.detail = ""

    def success(self, detail: str) -> None:
        self.status = "ok"
        self.detail = detail

    def failure(self, detail: str) -> None:
        self.status = "failed"
        self.detail = detail

    def skipped(self, detail: str) -> None:
        self.status = "skipped"
        self.detail = detail


def source_entry(produced: int, clone: Stage, work: Stage, url) -> dict:
    """把一个源的克隆阶段 + 加工阶段合成状态文件里的一项。"""
    if produced > 0:
        status = "ok"
    elif clone.status == "failed" or work.status == "failed":
        status = "failed"
    else:
        status = "skipped"
    if work.status == "ok":
        detail = work.detail
    elif clone.status == "failed":
        detail = clone.detail
    else:
        detail = work.detail or clone.detail
    if produced > 0 and clone.detail == "使用缓存" and work.status == "ok":
        detail = "使用缓存；" + work.detail
    return {"status": status, "url": url, "detail": detail}


def skip_existing(existing: int) -> int:
    """内容库已存在：不重新获取，只按现有内容补写状态文件。"""
    counts = stack_counts()
    sources = {
        "hello-algo": {"status": "skipped", "url": rel(SOURCES_DIR / "hello-algo"),
                       "detail": "内容库已存在，未重新获取"},
        "jwasham": {"status": "skipped", "url": rel(SOURCES_DIR / "jwasham"),
                    "detail": "内容库已存在，未重新获取"},
        "fetch": {"status": "skipped", "url": rel(TOOLS / "sources.json"),
                  "detail": "内容库已存在，未重新获取"},
    }
    status = build_content_status(counts, sources)
    print("内容库已存在，跳过（共 %d 篇；--force 可重建）" % existing, flush=True)
    try:
        print("内容状态：%s" % rel(write_content_status(status)), flush=True)
    except OSError as exc:
        warn("内容状态写入失败：%s" % exc)
    if "cs-basics" in status["missing"]:
        print_missing_algorithms(status)
        return 1
    return 0


def main(argv=None) -> int:
    global SOURCES_DIR
    parser = argparse.ArgumentParser(description="首次运行 / 重建内容库")
    parser.add_argument("--force", action="store_true", help="清掉已生成的内容后重建（保留 content/_meta）")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES),
                        help="源缓存目录（默认 data/.sources）")
    parser.add_argument("--print-sources", action="store_true",
                        help="只打印将尝试的内容源地址后退出，不读写内容")
    args = parser.parse_args(argv)

    if args.print_sources:
        return print_sources()

    SOURCES_DIR = Path(args.sources).resolve()
    existing = body_markdown_count()
    if existing and not args.force:
        return skip_existing(existing)

    started = time.time()
    try:
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        CONTENT.mkdir(parents=True, exist_ok=True)
        if args.force and existing:
            info("重建：清理已生成的内容（保留 content/_meta）")
            clear_generated()
        if not META.is_dir():
            warn("content/_meta 缺失；分类名称与计划模板可能不可用。"
                 "可运行 git checkout -- content/_meta 恢复")

        stages = [
            Stage("获取 hello-algo 源"),
            Stage("转换 hello-algo"),
            Stage("获取 jwasham 源"),
            Stage("拆分 jwasham"),
            Stage("抓取教程正文"),
        ]
        actual_urls = {"hello-algo": None, "jwasham": None}

        step("准备源缓存（%s）" % rel(SOURCES_DIR))
        hello_source = SOURCES_DIR / "hello-algo" / "docs"
        if hello_source.is_dir():
            stages[0].success("使用缓存")
            actual_urls["hello-algo"] = rel(SOURCES_DIR / "hello-algo")
            ok("hello-algo：使用缓存")
        else:
            try:
                url, attempts = clone_with_fallback(
                    OFFICIAL_HELLO_ALGO_URL,
                    HELLO_ALGO_ENV,
                    SOURCES_DIR / "hello-algo",
                    ["/docs/"],
                    "hello-algo",
                )
                actual_urls["hello-algo"] = url
                detail = "浅克隆完成（%s）" % url
                if attempts:
                    detail += "；镜像回退成功"
                stages[0].success(detail)
                ok(detail)
            except PrepareError as exc:
                actual_urls["hello-algo"] = source_candidates(
                    OFFICIAL_HELLO_ALGO_URL, HELLO_ALGO_ENV)[0][0]
                stages[0].failure(str(exc))
                warn("hello-algo：" + str(exc))

        if stages[0].status == "ok":
            step("转换 hello-algo 章节")
            try:
                detail = run_convert()
                stages[1].success(detail)
                ok(detail)
            except PrepareError as exc:
                stages[1].failure(str(exc))
                warn(str(exc))
        else:
            stages[1].skipped("源不可用")

        jwasham_source = SOURCES_DIR / "jwasham" / "translations" / "README-cn.md"
        if jwasham_source.is_file():
            stages[2].success("使用缓存")
            actual_urls["jwasham"] = rel(SOURCES_DIR / "jwasham")
            ok("jwasham：使用缓存")
        else:
            try:
                url, attempts = clone_with_fallback(
                    OFFICIAL_JWASHAM_URL,
                    JWASHAM_ENV,
                    SOURCES_DIR / "jwasham",
                    ["/translations/", "/programming-language-resources.md"],
                    "jwasham",
                )
                actual_urls["jwasham"] = url
                detail = "浅克隆完成（%s）" % url
                if attempts:
                    detail += "；镜像回退成功"
                stages[2].success(detail)
                ok(detail)
            except PrepareError as exc:
                actual_urls["jwasham"] = source_candidates(
                    OFFICIAL_JWASHAM_URL, JWASHAM_ENV)[0][0]
                stages[2].failure(str(exc))
                warn("jwasham：" + str(exc))

        if stages[2].status == "ok":
            step("拆分 jwasham 清单")
            try:
                detail = run_split()
                stages[3].success(detail)
                ok(detail)
            except PrepareError as exc:
                stages[3].failure(str(exc))
                warn(str(exc))
        else:
            stages[3].skipped("源不可用")

        step("抓取教程正文")
        try:
            detail = run_fetch()
            stages[4].success(detail)
            ok(detail)
        except PrepareError as exc:
            stages[4].failure(str(exc))
            warn(str(exc))

        counts = stack_counts()
        generated = body_markdown_count()
        succeeded = sum(1 for stage in stages if stage.status == "ok")
        failed = sum(1 for stage in stages if stage.status == "failed")
        sources = {
            "hello-algo": source_entry(counts.get("cs-basics", 0), stages[0], stages[1],
                                       actual_urls["hello-algo"]),
            "jwasham": source_entry(counts.get("interview", 0), stages[2], stages[3],
                                    actual_urls["jwasham"]),
            "fetch": {
                "status": stages[4].status if stages[4].status in ("ok", "failed") else "skipped",
                "url": rel(TOOLS / "sources.json"),
                "detail": stages[4].detail,
            },
        }
        status = build_content_status(counts, sources)
        status_file = write_content_status(status)

        print("", flush=True)
        print("汇总：成功 %d / 失败 %d / 跳过 %d，用时 %.0f 秒"
              % (succeeded, failed, sum(1 for s in stages if s.status == "skipped"),
                 time.time() - started), flush=True)
        for stage in stages:
            if stage.status == "ok":
                print("  ok   %-16s %s" % (stage.name, stage.detail), flush=True)
            elif stage.status == "failed":
                print("  !!   %-16s %s" % (stage.name, stage.detail), flush=True)
            else:
                print("  --   %-16s %s" % (stage.name, stage.detail), flush=True)
        print("内容库：%d 篇（%s）"
              % (generated, " / ".join("%s %d" % (name, count)
                                       for name, count in status["stacks"].items())), flush=True)
        print("内容状态：%s" % rel(status_file), flush=True)
        if failed:
            print("失败明细：%s" % rel(LOG_PATH), flush=True)
            print("提示：首次运行需要联网，且需要 git 命令可用；网络恢复后重跑本命令即可。", flush=True)
        if status["missing"]:
            print("!! 内容不完整：缺少 %s；服务仍可启动，但资料库对应大类会是空的。"
                  % missing_labels(status["missing"]), flush=True)

        # hello-algo 是算法内容的唯一来源：它整体失败时不能只靠“其它阶段成功”吞掉
        if "cs-basics" in status["missing"]:
            print_missing_algorithms(status)
            return 1
        if failed and succeeded == 0:
            return 1
        return 0
    except PrepareError as exc:
        print("准备内容失败：" + str(exc), flush=True)
        print("提示：首次运行需要联网，且需要 git 命令可用；网络恢复后重跑本命令即可。", flush=True)
        return 1
    except OSError as exc:
        print("准备内容失败：" + str(exc), flush=True)
        print("提示：请检查 content/ 与 data/.sources/ 的写入权限后重试。", flush=True)
        return 1
    except KeyboardInterrupt:
        print("已中断。", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
