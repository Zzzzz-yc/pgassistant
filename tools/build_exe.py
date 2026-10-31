#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 launch.py 打成单文件 pgassistant.exe，输出到仓库根目录。

用法::

    .venv\\Scripts\\python.exe tools\\build_exe.py

为什么输出到仓库根而不是 ``dist/``：``launch.py`` 在 frozen 模式下用
``Path(sys.executable).parent`` 当仓库根（onefile 的 ``__file__`` 指向临时解包
目录，不能用），所以 exe 必须和 ``backend/``、``frontend/``、``content/``、
``data/``、``.venv/`` 放在同一层。

构建产物（``pgassistant.exe`` / ``build/`` / ``*.spec``）都在 .gitignore 里，
不入库。PyInstaller 只是构建工具，不写进 backend/requirements*.txt；
没装时先执行：

    .venv\\Scripts\\python.exe -m pip install pyinstaller

脚本可重复运行：每次先删掉旧的 exe、build/ 和 *.spec，再重新构建。
退出码：成功 0；任何一步失败打印原因并以非 0 退出。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "launch.py"
TARGET = ROOT / "pgassistant.exe"
BUILD_DIR = ROOT / "build"
SPEC_DIR = BUILD_DIR
APP_NAME = "pgassistant"


class BuildError(Exception):
    """可预期的构建失败：打印原因后以非 0 退出。"""


def step(message: str) -> None:
    print(f"==> {message}", flush=True)


def info(message: str) -> None:
    print(f"  --  {message}", flush=True)


def ok(message: str) -> None:
    print(f"  ok  {message}", flush=True)


def warn(message: str) -> None:
    print(f"  !!  {message}", flush=True)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def clean_previous() -> None:
    """删掉上一次的产物，保证重复运行不会读到旧 exe 或旧 spec。"""
    step("清理旧产物")
    removed = 0
    if TARGET.exists():
        TARGET.unlink()
        ok(f"已删除 {TARGET.name}")
        removed += 1
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        ok(f"已删除 {BUILD_DIR.name}/")
        removed += 1
    # PyInstaller 默认把 .spec 写在 cwd；历史构建可能留在仓库根。
    for spec in ROOT.glob("*.spec"):
        spec.unlink()
        ok(f"已删除 {spec.name}")
        removed += 1
    if not removed:
        info("没有旧产物")


def check_pyinstaller() -> None:
    """确认当前解释器能跑 PyInstaller；缺了给出可复制的安装命令。"""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise BuildError(
            "当前 Python 没有安装 PyInstaller。先安装（只装构建工具，不进 requirements）：\n"
            f"      {sys.executable} -m pip install pyinstaller"
        )
    cp = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    version = (cp.stdout or cp.stderr or "").strip()
    if cp.returncode != 0:
        raise BuildError(f"PyInstaller 不可用（退出码 {cp.returncode}）：{version}")
    ok(f"PyInstaller {version}（{sys.executable}）")


def build() -> None:
    if not ENTRY.exists():
        raise BuildError(f"找不到入口文件：{ENTRY}")
    if sys.version_info < (3, 11):
        raise BuildError(
            f"当前 Python 是 {sys.version.split()[0]}，打包用的解释器需要 3.11+"
        )

    # --onefile：单文件，双击即用；--console：保留控制台看启动日志（不选 --windowed）。
    # --distpath 指到仓库根：frozen 模式用 exe 所在目录当 ROOT，exe 必须和仓库内容同层。
    # --workpath / --specpath 都放进 build/，避免仓库根散落中间产物。
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        APP_NAME,
        "--distpath",
        str(ROOT),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(SPEC_DIR),
        str(ENTRY),
    ]
    step("构建单文件 exe（首次约 1 分钟）")
    info("将执行：" + " ".join(cmd))
    cp = subprocess.run(cmd, cwd=str(ROOT))
    if cp.returncode != 0:
        raise BuildError(
            f"PyInstaller 构建失败（退出码 {cp.returncode}），原因见上面的输出。"
        )

    if not TARGET.exists():
        raise BuildError(f"构建结束但没找到 {TARGET}")
    size = TARGET.stat().st_size
    if size <= 0:
        raise BuildError(f"{TARGET} 是空文件（0 字节）")
    ok(f"已生成 {TARGET}（{human_size(size)}，{size} 字节）")


def main() -> int:
    try:
        step(f"仓库根：{ROOT}")
        check_pyinstaller()
        clean_previous()
        build()
        print("", flush=True)
        print(f"  完成：双击 {TARGET.name} 即可启动；首次使用请先签名：", flush=True)
        print("    powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_selfsigned.ps1", flush=True)
        print("", flush=True)
        return 0
    except BuildError as exc:
        warn(str(exc))
        return 1
    except OSError as exc:
        warn(f"文件操作失败：{exc}")
        return 1


if __name__ == "__main__":
    if os.name == "nt" and not sys.stdout.isatty():
        # 管道 / 重定向时 Windows 默认 GBK，显式 UTF-8 保住中文提示。
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover
            pass
    sys.exit(main())
