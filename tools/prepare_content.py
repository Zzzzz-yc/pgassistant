#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prepare_content.py — 首次运行 / 重建内容库。

用法::

    python tools/prepare_content.py            已有内容时跳过
    python tools/prepare_content.py --force    清掉生成的内容后重建

流程：

1. 源缓存 ``data/.sources/``（已 gitignore）：缺少时浅克隆
   - hello-algo：只取 ``docs/``
   - coding-interview-university：只取拆分脚本需要的文件
2. ``convert_content.py`` 转换 hello-algo 章节
3. ``split_jwasham.py`` 拆分 jwasham 清单
4. ``fetch_intro.py`` 按 ``tools/sources.json`` 抓取外站教程正文

``content/_meta/`` 是随仓库提供的手写元数据，本脚本不会覆盖或删除它。
单个源失败会记录并继续其它源；全部失败才以非 0 退出。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
CONTENT = ROOT / "content"
META = CONTENT / "_meta"
DEFAULT_SOURCES = ROOT / "data" / ".sources"
LOG_PATH = DEFAULT_SOURCES / "prepare-content.log"

HELLO_ALGO_URL = "https://github.com/krahets/hello-algo"
JWASHAM_URL = "https://github.com/jwasham/coding-interview-university"

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


def main(argv=None) -> int:
    global SOURCES_DIR
    parser = argparse.ArgumentParser(description="首次运行 / 重建内容库")
    parser.add_argument("--force", action="store_true", help="清掉已生成的内容后重建（保留 content/_meta）")
    parser.add_argument("--sources", default=str(DEFAULT_SOURCES),
                        help="源缓存目录（默认 data/.sources）")
    args = parser.parse_args(argv)

    SOURCES_DIR = Path(args.sources).resolve()
    existing = body_markdown_count()
    if existing and not args.force:
        print("内容库已存在，跳过（共 %d 篇；--force 可重建）" % existing, flush=True)
        return 0

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

        step("准备源缓存（%s）" % rel(SOURCES_DIR))
        hello_source = SOURCES_DIR / "hello-algo" / "docs"
        if hello_source.is_dir():
            stages[0].success("使用缓存")
            ok("hello-algo：使用缓存")
        else:
            try:
                clone_sparse(HELLO_ALGO_URL, SOURCES_DIR / "hello-algo", ["/docs/"], "hello-algo")
                stages[0].success("浅克隆完成")
                ok("hello-algo：浅克隆完成")
            except PrepareError as exc:
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
            ok("jwasham：使用缓存")
        else:
            try:
                clone_sparse(
                    JWASHAM_URL,
                    SOURCES_DIR / "jwasham",
                    ["/translations/", "/programming-language-resources.md"],
                    "jwasham",
                )
                stages[2].success("浅克隆完成")
                ok("jwasham：浅克隆完成")
            except PrepareError as exc:
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

        generated = body_markdown_count()
        succeeded = sum(1 for stage in stages if stage.status == "ok")
        failed = sum(1 for stage in stages if stage.status == "failed")

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
        print("内容库：%d 篇" % generated, flush=True)
        if failed:
            print("失败明细：%s" % rel(LOG_PATH), flush=True)
            print("提示：首次运行需要联网，且需要 git 命令可用；网络恢复后重跑本命令即可。", flush=True)

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
