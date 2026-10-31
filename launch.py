#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pgassistant 一键启动 / 停止。

单文件、只用 Python 标准库，Windows / macOS / Linux 共用同一套实现。
启动与停止的全部逻辑都在这里；``start.cmd`` / ``stop.cmd`` / ``start.sh`` /
``stop.sh`` 只是「找到 Python 并转发参数」的薄壳，不再依赖 PowerShell 脚本
（也就不存在执行策略 / 数字签名的问题）。

用法::

    python launch.py                     启动（默认在 8000-8010 里挑第一个空闲端口）
    python launch.py start               同上
    python launch.py stop                停止（先按 runtime.json 里的 pid，再按端口兜底）

启动参数（旧 PowerShell 写法与 GNU 写法等价，大小写不敏感）::

    -NoBrowser / --no-browser            只起服务，不开浏览器
    -Dev       / --dev                   开发态：设 PG_DEV_ORIGINS，另起 Vite :5173
    --port <n>                           跳过端口扫描，直接用 n
    --runtime-file <path>                运行状态文件（默认 data/runtime.json）
    --dry-run                            只打印将要做什么，不执行任何副作用

停止参数::

    --runtime-file <path>                读哪个运行状态文件
    --port <n>                           状态文件缺失时用它做端口兜底
    --dry-run                            只打印将要做的事，不杀进程、不删文件

退出码：成功 0；任何一步失败非 0，并打印原因。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
VENV = ROOT / ".venv"
DEFAULT_RUNTIME = ROOT / "data" / "runtime.json"
BOOTSTRAP = BACKEND / "scripts" / "bootstrap_index.py"
PORT_START = 8000
PORT_END = 8010
VITE_PORT = 5173
IS_WINDOWS = os.name == "nt"
STILL_ACTIVE = 259

# 统一按 UTF-8 输出：管道 / 重定向下 Windows 默认用 GBK（cp936），
# 中文会变成乱码；UTF-8 在 cmd 控制台（PEP 528 走 WriteConsoleW）同样正常。
# 行缓冲让后台运行时日志能实时读到。
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True, errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True, errors="replace")
except Exception:  # pragma: no cover - 老解释器 / 特殊流
    pass

# 本机代理环境变量不该拦截 127.0.0.1 的健康检查。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LaunchError(Exception):
    """可预期的启动 / 停止失败：打印原因后以非 0 退出。"""


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def step(message: str) -> None:
    print(f"==> {message}", flush=True)


def ok(message: str) -> None:
    print(f"  ok  {message}", flush=True)


def info(message: str) -> None:
    print(f"  --  {message}", flush=True)


def warn(message: str) -> None:
    print(f"  !!  {message}", flush=True)


def fatal(message: str) -> None:
    raise LaunchError(message)


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def tail(cp: subprocess.CompletedProcess, lines: int = 8) -> str:
    text = ((cp.stdout or "") + (cp.stderr or "")).strip()
    if not text:
        return ""
    return "\n".join(text.splitlines()[-lines:])


# --------------------------------------------------------------------------
# 进程 / 端口 / HTTP 小工具
# --------------------------------------------------------------------------
def run(cmd, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """跑一个外部命令并捕获输出；不抛异常（调用方看 returncode）。"""
    try:
        return subprocess.run(
            [str(part) for part in cmd],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise LaunchError(f"无法执行 {cmd[0]}：{exc}") from exc


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with _OPENER.open(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def wait_http(url: str, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if http_ok(url):
            return True
        time.sleep(0.4)
    return False


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def python_ok(exe, preargs=()) -> bool:
    """真跑一次 --version：PATH 上的 python 可能是 WindowsApps 占位符。"""
    try:
        cp = subprocess.run(
            [str(exe), *preargs, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.search(r"Python (\d+)\.(\d+)", (cp.stdout or "") + (cp.stderr or ""))
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (3, 11)


def find_system_python():
    """返回 (可执行文件, 额外参数)；找不到返回 None。"""
    candidates = []
    if sys.executable:
        candidates.append((sys.executable, ()))
    if IS_WINDOWS:
        launcher = shutil.which("py")
        if launcher:
            for version in ("-3.13", "-3.12", "-3.11", "-3"):
                candidates.append((launcher, (version,)))
    for name in ("python3.13", "python3.12", "python3.11", "python3", "python"):
        exe = shutil.which(name)
        if not exe or "windowsapps" in exe.lower():
            continue
        candidates.append((exe, ()))

    seen = set()
    for exe, preargs in candidates:
        key = (os.path.normcase(str(exe)), preargs)
        if key in seen:
            continue
        seen.add(key)
        if python_ok(exe, preargs):
            return exe, preargs
    return None


def find_node():
    """返回 (node 可执行文件, 版本号)；版本不够时也返回版本号以便报错。"""
    exe = shutil.which("node")
    if not exe:
        return None, None
    cp = run([exe, "--version"])
    version = (cp.stdout or cp.stderr or "").strip()
    match = re.match(r"v(\d+)\.", version)
    if cp.returncode != 0 or not match or int(match.group(1)) < 20:
        return None, version or None
    return exe, version


def find_npm():
    # npm.cmd 优先：避免 PowerShell 执行策略拦住 npm.ps1（虽然本脚本不再走 PS，
    # 但 cmd 壳里 cmd.exe 直接执行 npm.cmd 同样最稳）。
    for name in ("npm.cmd", "npm"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port() -> int | None:
    for port in range(PORT_START, PORT_END + 1):
        if is_port_free(port):
            return port
    return None


def deps_ready(py: Path) -> bool:
    cp = run([py, "-c", "import fastapi, uvicorn, httpx, pydantic"])
    return cp.returncode == 0


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_command_line(pid: int) -> str | None:
    """取进程命令行；取不到（权限不足 / 进程已退）返回 None。"""
    if IS_WINDOWS:
        wmic = shutil.which("wmic")
        if wmic:
            cp = run([wmic, "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/format:list"])
            for line in (cp.stdout or "").splitlines():
                if line.lower().startswith("commandline="):
                    value = line.split("=", 1)[1].strip()
                    if value:
                        return value
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell:
            script = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
            cp = run([powershell, "-NoProfile", "-NonInteractive", "-Command", script])
            value = (cp.stdout or "").strip()
            if value:
                return value
        return None

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        if raw:
            return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except OSError:
        pass
    ps = shutil.which("ps")
    if ps:
        cp = run([ps, "-p", str(pid), "-o", "command="])
        value = (cp.stdout or "").strip()
        if value:
            return value
    return None


def classify_pid(pid, tokens) -> tuple[str, str | None]:
    """返回 (gone | project | other | unknown, 命令行)。

    - gone / unknown 都算「pid 无效」，交给端口兜底；
    - other 是 pid 被别的进程复用了，绝不能杀。
    """
    if not isinstance(pid, int) or pid <= 0:
        return "gone", None
    if not pid_alive(pid):
        return "gone", None
    cmdline = process_command_line(pid)
    if not cmdline:
        return "unknown", None
    lowered = cmdline.lower()
    if any(token.lower() in lowered for token in tokens):
        return "project", cmdline
    return "other", cmdline


def kill_tree(pid: int) -> bool:
    """先 terminate，必要时 kill。Windows 用 taskkill /T /F；POSIX 走 SIGTERM→SIGKILL。"""
    if not pid_alive(pid):
        return True
    if IS_WINDOWS:
        cp = run(["taskkill", "/PID", str(pid), "/T", "/F"])
        if cp.returncode == 0:
            return True
        # 128：进程不存在（执行期间自己退了）；否则看最终状态。
        return not pid_alive(pid)

    def send(sig) -> bool:
        try:
            os.killpg(pid, sig)  # 启动时 start_new_session=True，pid 即进程组
            return True
        except ProcessLookupError:
            return False
        except OSError:
            try:
                os.kill(pid, sig)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return False

    if not send(signal.SIGTERM):
        return not pid_alive(pid)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    send(getattr(signal, "SIGKILL", signal.SIGTERM))
    time.sleep(0.2)
    return not pid_alive(pid)


def find_listener_pids(port: int):
    """返回监听该端口的 pid 列表；平台缺少探测工具时返回 None（无法判断）。"""
    if IS_WINDOWS:
        cp = run(["netstat", "-ano", "-p", "TCP"])
        pids = set()
        for line in (cp.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            if parts[3].upper() != "LISTENING":
                continue
            if parts[1].rsplit(":", 1)[-1] != str(port):
                continue
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            if pid > 0:
                pids.add(pid)
        return sorted(pids)

    lsof = shutil.which("lsof")
    if lsof:
        cp = run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        if cp.returncode in (0, 1):
            pids = []
            for line in (cp.stdout or "").splitlines():
                value = line.strip()
                if value.isdigit():
                    pids.append(int(value))
            return sorted(set(pids))
    ss = shutil.which("ss")
    if ss:
        cp = run([ss, "-ltnp"])
        pids = set()
        for line in (cp.stdout or "").splitlines():
            if "LISTEN" not in line:
                continue
            parts = line.split()
            if len(parts) < 4 or parts[3].rsplit(":", 1)[-1] != str(port):
                continue
            pids.update(int(value) for value in re.findall(r"pid=(\d+)", line))
        return sorted(pids)
    return None


def looks_like_our_server(cmdline: str | None) -> bool:
    """端口兜底时的强校验：命令行里既有 uvicorn/app.main，又指向本仓库。

    只认「命令行直接带仓库路径」的进程（我们的 venv python 一定带）。这样
    别人的 :8000 服务、别的 checkout 的实例都不会被误杀。
    """
    if not cmdline:
        return False
    lowered = cmdline.lower()
    if "uvicorn" not in lowered and "app.main" not in lowered:
        return False
    return str(ROOT).lower() in lowered or str(VENV).lower() in lowered


# --------------------------------------------------------------------------
# 运行状态文件
# --------------------------------------------------------------------------
def runtime_path(opts) -> Path:
    raw = getattr(opts, "runtime_file", None)
    if not raw:
        return DEFAULT_RUNTIME
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (ROOT / path)


def load_runtime(path: Path):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warn(f"runtime 文件读不了（{rel(path)}）：{exc}")
        return None
    return data if isinstance(data, dict) else None


def write_runtime(path: Path, url: str, port: int, pid: int, dev: bool, vite_pid: int | None) -> None:
    """只写「怎么连上它」，token 绝不落盘（需求文档 §13.7）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": url,
        "host": "127.0.0.1",
        "port": port,
        "pid": pid,
        "vite_pid": vite_pid,
        "dev": bool(dev),
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
class Options:
    def __init__(self) -> None:
        self.command = "start"
        self.no_browser = False
        self.dev = False
        self.port: int | None = None
        self.runtime_file: str | None = None
        self.dry_run = False
        self.help = False


def parse_port(raw: str) -> int:
    try:
        port = int(raw, 10)
    except (TypeError, ValueError):
        raise LaunchError(f"端口不是整数：{raw}")
    if not 1 <= port <= 65535:
        raise LaunchError(f"端口超出范围 1-65535：{port}")
    return port


def parse_args(argv) -> Options:
    opts = Options()
    positionals = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        low = arg.lower()
        if low in ("-h", "--help", "-?", "/?"):
            opts.help = True
        elif low in ("-nobrowser", "--no-browser", "--nobrowser"):
            opts.no_browser = True
        elif low in ("-dev", "--dev"):
            opts.dev = True
        elif low in ("-dryrun", "-dry-run", "--dry-run", "--dryrun"):
            opts.dry_run = True
        elif low in ("-port", "--port"):
            index += 1
            if index >= len(argv):
                raise LaunchError("--port 后面要跟端口号")
            opts.port = parse_port(argv[index])
        elif low.startswith("--port=") or low.startswith("-port="):
            opts.port = parse_port(arg.split("=", 1)[1])
        elif low in ("-runtime-file", "--runtime-file", "-runtimefile", "--runtimefile"):
            index += 1
            if index >= len(argv):
                raise LaunchError("--runtime-file 后面要跟路径")
            opts.runtime_file = argv[index]
        elif (
            low.startswith("--runtime-file=")
            or low.startswith("-runtime-file=")
            or low.startswith("--runtimefile=")
        ):
            opts.runtime_file = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            raise LaunchError(f"未知参数：{arg}（用 --help 看用法）")
        else:
            positionals.append(arg)
        index += 1

    if positionals:
        command = positionals[0].lower()
        if command not in ("start", "stop"):
            raise LaunchError(f"未知子命令：{positionals[0]}（只支持 start / stop）")
        if len(positionals) > 1:
            raise LaunchError(f"多余的参数：{' '.join(positionals[1:])}")
        opts.command = command
    return opts


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------
def cmd_start(opts: Options) -> int:
    dry = opts.dry_run
    if sys.version_info < (3, 11):
        fatal(
            "当前解释器是 Python "
            f"{sys.version.split()[0]}，需要 3.11 或更新版本。\n"
            "      从 https://www.python.org/downloads/ 安装 3.11+ 后重开终端，再运行：python launch.py\n"
            "      Windows 也可以用官方启动器：py -3.12 launch.py"
        )

    # ---------------------------------------------------------------- 1. 工具链
    step("检查工具链")
    ok(f"python: {sys.version.split()[0]}（{sys.executable}）")

    py = venv_python()
    venv_ready = False
    if py.exists():
        if not python_ok(py):
            fatal("现有 .venv 的 Python 低于 3.11；删掉 .venv 目录后重跑本脚本。")
        venv_ready = True
        ok(f".venv 已存在（{rel(py)}）")
    else:
        info(".venv 不存在，将创建")

    system_python = None
    if not venv_ready:
        system_python = find_system_python()
        if not system_python:
            fatal(
                "找不到 Python >= 3.11。\n"
                "      从 https://www.python.org/downloads/ 安装 3.11 或更新版本，重开终端后再运行。\n"
                "      Windows 也可以先装官方启动器 py，再用 py -3 launch.py。"
            )
        exe, preargs = system_python
        ok(f"用 {exe} {' '.join(preargs)} 创建 .venv".rstrip())

    node, node_version = find_node()
    if not node:
        detail = f"（检测到 {node_version}）" if node_version else ""
        fatal(
            f"找不到 Node >= 20{detail}。\n"
            "      从 https://nodejs.org/en/download 安装 Node 20+（LTS）后重开终端。"
        )
    npm = find_npm()
    if not npm:
        fatal("找不到 npm（随 Node 一起安装）；重装 Node 后再试。")
    ok(f"node: {node_version}（{node}）")
    ok(f"npm:  {npm}")

    # ------------------------------------------------------------ 2. Python 环境
    step("准备 Python 环境（.venv）")
    created_venv = False
    if not venv_ready:
        exe, preargs = system_python
        cmd = [exe, *preargs, "-m", "venv", str(VENV)]
        if dry:
            info(f"将执行：{' '.join(cmd)}")
        else:
            cp = run(cmd, cwd=ROOT)
            if cp.returncode != 0:
                extra = tail(cp)
                fatal(f"python -m venv .venv 失败（退出码 {cp.returncode}）。{chr(10) + extra if extra else ''}")
            created_venv = True
            venv_ready = True
            ok("已创建 .venv")
    else:
        ok(".venv 已存在，跳过创建")

    # 依赖检查本身是只读的，dry-run 也真跑一次，输出的判断才和实际执行一致。
    ready = deps_ready(py) if py.exists() else False
    install_deps = created_venv or not ready

    if install_deps:
        step("安装 backend/requirements.txt（首次会慢一点）")
        cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r",
               str(BACKEND / "requirements.txt")]
        if dry:
            info(f"将执行：{' '.join(cmd)}")
        else:
            cp = run(cmd, cwd=ROOT)
            if cp.returncode != 0:
                extra = tail(cp)
                fatal(
                    f"pip install 失败（退出码 {cp.returncode}）。网络慢可换镜像：\n"
                    "      PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple "
                    f"{py} -m pip install -r backend/requirements.txt"
                    + (f"\n{extra}" if extra else "")
                )
            ok("Python 依赖就绪")
    else:
        ok("Python 依赖已就绪，跳过安装")

    # ---------------------------------------------------------------- 3. 前端构建
    dist_index = FRONTEND / "dist" / "index.html"
    if dist_index.exists():
        ok("frontend/dist 已存在，跳过构建（改了前端代码请先 npm run build）")
    else:
        step("构建前端（frontend/dist 不存在）")
        node_modules = FRONTEND / "node_modules"
        installer = "ci" if (FRONTEND / "package-lock.json").exists() else "install"
        if node_modules.exists():
            info("node_modules 已存在，跳过安装")
        elif dry:
            info(f"将执行：{npm} {installer} --no-audit --no-fund（cwd=frontend）")
        else:
            cp = run([npm, installer, "--no-audit", "--no-fund"], cwd=FRONTEND)
            if cp.returncode != 0:
                extra = tail(cp)
                fatal(
                    f"npm {installer} 失败（退出码 {cp.returncode}）。网络问题可加 "
                    "--registry=https://registry.npmmirror.com 重试一次。"
                    + (f"\n{extra}" if extra else "")
                )
        if dry:
            info(f"将执行：{npm} run build（cwd=frontend）")
        else:
            cp = run([npm, "run", "build"], cwd=FRONTEND)
            if cp.returncode != 0:
                extra = tail(cp)
                fatal(f"npm run build 失败（退出码 {cp.returncode}）。" + (f"\n{extra}" if extra else ""))
            if not dist_index.exists():
                fatal("构建结束但 frontend/dist/index.html 仍不存在。")
            ok("前端构建完成")

    # -------------------------------------------------------------------- 4. 索引
    step("准备数据库与索引（幂等，内容没变会跳过）")
    if dry:
        info(f"将执行：{py} {rel(BOOTSTRAP)}")
    else:
        cp = run([py, BOOTSTRAP], cwd=ROOT)
        if cp.returncode != 0:
            extra = tail(cp)
            fatal(f"bootstrap_index.py 失败（退出码 {cp.returncode}）。" + (f"\n{extra}" if extra else ""))
        ok("索引就绪")

    # -------------------------------------------------------------------- 5. 端口
    step("选择端口")
    if opts.port is not None:
        port = opts.port
        if not is_port_free(port):
            fatal(
                f"端口 {port} 已被占用（--port 指定时不会自动换端口）。\n"
                f"      换一个 --port，或先停掉占用它的进程（netstat -ano | findstr :{port}）。"
            )
        info(f"使用 --port 指定的 {port}")
    else:
        port = find_free_port()
        if port is None:
            fatal(f"{PORT_START}-{PORT_END} 都被占用了，先关掉其它实例再试。")
        info(f"{PORT_START}-{PORT_END} 中第一个空闲端口：{port}")

    # ---------------------------------------------------------------- 6. 起服务
    base_url = f"http://127.0.0.1:{port}"
    runtime = runtime_path(opts)
    env = os.environ.copy()
    env["PG_PORT"] = str(port)
    env["PYTHONPATH"] = str(BACKEND)
    if opts.dev:
        # 开发态：Vite :5173 的写请求要过 Origin 白名单
        env["PG_DEV_ORIGINS"] = f"http://127.0.0.1:{VITE_PORT}"
        env["PG_BACKEND_ORIGIN"] = base_url

    uvicorn_cmd = [str(py), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
    step(f"启动 uvicorn（{base_url}，只绑定 127.0.0.1）")

    if dry:
        info(f"将执行（cwd=backend）：{' '.join(uvicorn_cmd)}")
        info(f"将设置 PG_PORT={port}" + (f"，PG_DEV_ORIGINS=http://127.0.0.1:{VITE_PORT}" if opts.dev else ""))
        info(f"将等待 {base_url}/api/health 返回 200（最多 45 秒）")
        info(f"将写入 {rel(runtime)}（url/host/port/pid/dev/started_at/vite_pid，不含 token）")
        if opts.dev:
            info(f"将另起 Vite（:{VITE_PORT}，代理 /api 到 {base_url}）并把 vite_pid 写进 runtime 文件")
        if opts.no_browser:
            info("按 --no-browser 不打开浏览器")
        else:
            target = f"http://127.0.0.1:{VITE_PORT}" if opts.dev else base_url
            info(f"将打开浏览器 {target}")
        print("\ndry-run 完成：以上为将要执行的步骤，未产生任何改动。", flush=True)
        return 0

    # POSIX 上开新会话，stop / Ctrl+C 才能用 killpg 把整棵进程树（npm → node）收掉；
    # Windows 用 taskkill /T，start_new_session 无意义（传了也会被忽略）。
    server = subprocess.Popen(
        uvicorn_cmd,
        cwd=str(BACKEND),
        env=env,
        **({} if IS_WINDOWS else {"start_new_session": True}),
    )
    vite = None
    runtime_written = False
    try:
        deadline = time.time() + 45
        ready = False
        while time.time() < deadline:
            code = server.poll()
            if code is not None:
                fatal(f"uvicorn 提前退出（退出码 {code}），看上面的报错。")
            if http_ok(f"{base_url}/api/health"):
                ready = True
                break
            time.sleep(0.4)
        if not ready:
            fatal(f"45 秒内 /api/health 没起来，检查端口 {port} 与 data/logs/app.log。")
        ok(f"uvicorn 就绪（pid {server.pid}）")

        write_runtime(runtime, base_url, port, server.pid, opts.dev, None)
        runtime_written = True
        ok(f"已写入 {rel(runtime)}（不含 token）")

        open_url = base_url
        if opts.dev:
            step(f"启动 Vite dev server（:{VITE_PORT}，代理 /api 到 {base_url}）")
            vite_cmd = [npm, "run", "dev", "--", "--port", str(VITE_PORT)]
            vite = subprocess.Popen(
                vite_cmd,
                cwd=str(FRONTEND),
                env=env,
                **({} if IS_WINDOWS else {"start_new_session": True}),
            )
            if wait_http(f"http://127.0.0.1:{VITE_PORT}/", 60):
                open_url = f"http://127.0.0.1:{VITE_PORT}"
                ok("Vite 就绪")
            else:
                warn("Vite 60 秒内没起来，先按生产地址打开；看 npm run dev 的报错")
            write_runtime(runtime, base_url, port, server.pid, True, vite.pid)

        print("", flush=True)
        print("  pgassistant 已启动", flush=True)
        print(f"    站点      {open_url}", flush=True)
        print(f"    接口文档  {base_url}/docs", flush=True)
        print('    令牌      在页面 <head> 的 <meta name="x-app-token"> 里（不落盘，随重启失效）', flush=True)
        print("    停止      Ctrl+C（或另开终端：python launch.py stop）", flush=True)
        print("", flush=True)

        if not opts.no_browser:
            try:
                opened = webbrowser.open(open_url)
            except Exception:
                opened = False
            if not opened:
                warn(f"没能自动打开浏览器，请手动访问 {open_url}")

        try:
            server.wait()
        except KeyboardInterrupt:
            print("\n  收到 Ctrl+C，正在停止…", flush=True)
        ok("服务已退出")
        return 0
    finally:
        for process in (vite, server):
            if process is not None and process.poll() is None:
                kill_tree(process.pid)
        if runtime_written:
            try:
                runtime.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                warn(f"清理 {rel(runtime)} 失败：{exc}")


# --------------------------------------------------------------------------
# 停止
# --------------------------------------------------------------------------
def as_int(value, minimum: int, maximum: int | None = None) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < minimum or (maximum is not None and number > maximum):
        return None
    return number


def stop_pid(pid, tokens, label, killed, skipped) -> None:
    status, _cmdline = classify_pid(pid, tokens)
    if status == "gone":
        info(f"{label} pid {pid} 已不存在")
        return
    if status == "project":
        if kill_tree(pid):
            ok(f"已停止 {label}（pid {pid}）")
            killed.append((label, pid))
        else:
            raise LaunchError(f"停止 {label}（pid {pid}）失败：权限不足或被保护，可用管理员权限重试。")
        return
    if status == "other":
        warn(f"pid {pid} 已被别的进程复用（命令行不是 {label}），跳过")
        skipped.append(pid)
    else:
        warn(f"读不到 pid {pid} 的命令行，改用端口判定（避免误杀）")


def cmd_stop(opts: Options) -> int:
    dry = opts.dry_run
    runtime = runtime_path(opts)
    state = load_runtime(runtime)
    if state is None and runtime.exists():
        warn(f"runtime 文件无效，按「没有状态文件」处理：{rel(runtime)}")

    server_pid = as_int(state.get("pid"), 1) if state else None
    vite_pid = as_int(state.get("vite_pid"), 1) if state else None
    known_port = as_int(state.get("port"), 1, 65535) if state else None

    ports = []
    if known_port:
        ports.append(known_port)
    if opts.port is not None and opts.port not in ports:
        ports.append(opts.port)
    if not ports:
        ports = list(range(PORT_START, PORT_END + 1))

    step("停止 pgassistant" + ("（dry-run）" if dry else ""))
    if state is None:
        info(f"没有 runtime 文件（{rel(runtime)}），按端口兜底查找监听者")
    if opts.port is not None and not known_port:
        info(f"按 --port {opts.port} 兜底")

    killed = []
    skipped = []
    planned = 0
    vite_killed = False
    summary_port = known_port or opts.port

    # 先停 Vite 再停后端：后端一退出，前台 launch.py 的 finally 会立刻清 Vite，
    # 两边同时 taskkill 同一棵树会让 stop 读不到命令行、把「Vite 已停」误报成「否」。
    if not dry:
        if vite_pid:
            before = len(killed)
            stop_pid(vite_pid, ("vite", "npm", "node"), "Vite", killed, skipped)
            vite_killed = len(killed) > before
        if server_pid:
            stop_pid(server_pid, ("uvicorn", "app.main"), "uvicorn", killed, skipped)
    else:
        for pid, tokens, label in (
            (vite_pid, ("vite", "npm", "node"), "Vite"),
            (server_pid, ("uvicorn", "app.main"), "uvicorn"),
        ):
            if not pid:
                continue
            status, _cmdline = classify_pid(pid, tokens)
            if status == "project":
                planned += 1
                info(f"将停止 {label}（pid {pid}，含子进程树）")
            elif status == "gone":
                info(f"{label} pid {pid} 已不存在")
            else:
                warn(f"pid {pid} 不确认是本项目的 {label}（{status}），将改用端口判定")

    # 端口兜底：runtime 缺失 / pid 无效 / pid 杀完端口仍在监听时都走这里。
    # 只认命令行里带本仓库路径的 uvicorn，别人的 :8000 服务绝不碰。
    for port in ports:
        listener_pids = find_listener_pids(port)
        if listener_pids is None:
            warn("没找到 lsof 或 ss，跳过端口兜底；请手动确认端口已释放。")
            break
        for pid in listener_pids:
            if pid in skipped or pid <= 0:
                continue
            if any(pid == killed_pid for _label, killed_pid in killed):
                continue
            cmdline = process_command_line(pid)
            if looks_like_our_server(cmdline):
                if dry:
                    planned += 1
                    info(f"端口 {port} 的监听进程是本项目的 uvicorn（pid {pid}），将停止")
                elif kill_tree(pid):
                    ok(f"已停止端口 {port} 上的残留 uvicorn（pid {pid}）")
                    killed.append((f"端口 {port} 的 uvicorn", pid))
                    if summary_port is None:
                        summary_port = port
                else:
                    raise LaunchError(f"停止端口 {port} 的 pid {pid} 失败：权限不足或被保护。")
            elif cmdline:
                warn(f"端口 {port} 被 pid {pid} 占用（不是本项目启动的），已跳过")
            else:
                warn(f"端口 {port} 被 pid {pid} 占用，但读不到它的命令行，已跳过（避免误杀）")

    if dry:
        if planned:
            info(f"dry-run：将停止 {planned} 个进程")
        else:
            info("当前没有在运行的实例")
        info(f"将删除 {rel(runtime)}（如果存在）")
        print("\ndry-run 完成：以上为将要执行的步骤，未产生任何改动。", flush=True)
        return 0

    errors = []
    if killed:
        if runtime.exists():
            try:
                runtime.unlink()
            except OSError as exc:
                errors.append(f"删除 {rel(runtime)} 失败：{exc}")
            else:
                ok(f"已清理 {rel(runtime)}")
        print(
            f"\n已停止 {summary_port if summary_port is not None else '本机实例'} / "
            f"Vite: {'是' if vite_killed else '否'} / 进程数 {len(killed)}",
            flush=True,
        )
        if errors:
            for message in errors:
                warn(message)
            return 1
        return 0

    print("\n  当前没有在运行的实例", flush=True)
    if runtime.exists():
        # 端口上没有可确认的本项目进程时，仍清掉过期的 runtime 文件，
        # 否则下次启动会继续拿一个死 pid 当线索。
        try:
            runtime.unlink()
            ok(f"已清理过期的 {rel(runtime)}")
        except OSError as exc:
            warn(f"删除 {rel(runtime)} 失败：{exc}")
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
HELP = __doc__


def main(argv) -> int:
    try:
        opts = parse_args(argv)
        if opts.help:
            print(HELP)
            return 0
        if opts.command == "stop":
            return cmd_stop(opts)
        return cmd_start(opts)
    except LaunchError as exc:
        print(f"  !!  {exc}", flush=True)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n  已取消（Ctrl+C）", flush=True)
        sys.exit(130)
