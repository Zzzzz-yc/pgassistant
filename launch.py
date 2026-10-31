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
    python launch.py stop                停止（runtime.json 的 pid → health 探针 → 端口扫描）

启动参数（旧 PowerShell 写法与 GNU 写法等价，大小写不敏感）::

    -NoBrowser / --no-browser            只起服务，不开浏览器
    -Dev       / --dev                   开发态：设 PG_DEV_ORIGINS，另起 Vite :5173
    --port <n>                           跳过端口扫描，直接用 n
    --runtime-file <path>                运行状态文件（默认 data/runtime.json）
    --dry-run                            只打印将要做什么，不执行任何副作用

停止参数::

    --runtime-file <path>                读哪个运行状态文件
    --port <n>                           只检查这个端口（可叠加；不给则扫 8000-8010）
    --dry-run                            只打印将要做的事，不杀进程、不删文件

停止时的归属判定不依赖「读取别的进程命令行」：命令行读不到时，用
GET /api/health 探针确认端口上是不是 pgassistant；认不出归属就不杀。
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
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# 根目录必须按「程序放在哪」推导，而不是按 __file__：
# PyInstaller 的 --onefile 会把 launch.py 解包到系统临时目录（sys._MEIPASS）再执行，
# 此时 __file__ 指向 %TEMP%\_MEIxxxx，照它算 ROOT 就会去临时目录下找 backend/、
# frontend/、content/、data/ 和 .venv/，全部找不到。frozen 模式下 sys.executable
# 才是用户双击的那个 pgassistant.exe（onefile 不会改它的值），而 exe 固定放在仓库根，
# 所以它的父目录就是仓库根；未打包时保持原来的 __file__ 逻辑。
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
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


def probe_health(port: int, timeout: float = 2.0) -> tuple[bool, str]:
    """HTTP 探针：确认端口上跑的是不是 pgassistant，不依赖读进程命令行。

    ``/api/health`` 免令牌，所以不看 ``X-App-Token``；返回 (是否本项目, 说明)。
    判据：HTTP 200 且 JSON 同时含 ``status == "ok"``、``db_ready``、``version``。
    """
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with _OPENER.open(url, timeout=timeout) as response:
            code = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:  # 有 HTTP 响应，但不是 200
        return False, f"health 返回 HTTP {exc.code}，不是 pgassistant"
    except Exception as exc:  # 连接被拒 / 超时 / 根本不是 HTTP
        return False, f"health 探针不通（{exc.__class__.__name__}）"
    if code != 200:
        return False, f"health 返回 HTTP {code}，不是 pgassistant"
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return False, "health 响应不是 JSON，不是 pgassistant"
    if not isinstance(data, dict):
        return False, "health 响应不是 JSON 对象，不是 pgassistant"
    missing = [key for key in ("status", "db_ready", "version") if key not in data]
    if missing:
        return False, f"health JSON 缺少 {'/'.join(missing)}，不是 pgassistant"
    if data.get("status") != "ok":
        return False, f"health 的 status 是 {data.get('status')!r}，不是 pgassistant"
    return True, "health 探针确认是 pgassistant"


def probe_is_pgassistant(port: int, timeout: float = 2.0) -> bool:
    """端口上是否是 pgassistant：只信 HTTP health 探针，不读命令行。"""
    return probe_health(port, timeout)[0]


def wait_port_released(port: int, seconds: float) -> bool | None:
    """等端口不再有 LISTENING；None 表示本平台没有可用的「端口 → pid」工具。"""
    deadline = time.time() + seconds
    while True:
        pids = find_listener_pids(port)
        if pids is None:
            return None
        if not pids:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.2)


def force_kill(pid: int) -> bool:
    """强杀单个 pid（Windows taskkill /T /F；POSIX SIGKILL），失败返回 False。"""
    if not pid_alive(pid):
        return True
    if IS_WINDOWS:
        run(["taskkill", "/PID", str(pid), "/T", "/F"])
        return not pid_alive(pid)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return False
    time.sleep(0.2)
    return not pid_alive(pid)


def ensure_port_released(port: int) -> None:
    """已确认归属的监听者被杀后复核端口：最多等 3s，未释放则强杀，仍失败抛错。"""
    released = wait_port_released(port, 3.0)
    if released is None:
        raise LaunchError(f"停止后无法确认端口 {port} 是否释放（缺少 netstat / lsof / ss）。")
    if released:
        return
    pids = find_listener_pids(port) or []
    # 只有探针仍确认是本项目时才强杀：这几秒里可能有新进程抢到同一端口。
    ours, detail = probe_health(port)
    if not ours:
        raise LaunchError(
            f"停止后端口 {port} 仍被 pid {'/'.join(map(str, pids)) or '未知'} 监听，"
            f"但 health 探针已无法确认是 pgassistant（{detail}）；为避免误杀未强杀，"
            f"请手动检查：netstat -ano | findstr :{port}"
        )
    if not pids:
        raise LaunchError(f"停止后端口 {port} 仍被监听，但查不到占用者 pid；请手动检查。")
    warn(f"端口 {port} 3 秒内未释放（pid {'/'.join(map(str, pids))}），升级为强杀")
    for pid in pids:
        force_kill(pid)
    released = wait_port_released(port, 2.0)
    if released is not True:
        remaining = find_listener_pids(port) or []
        raise LaunchError(
            f"强杀后端口 {port} 仍未释放（pid {'/'.join(map(str, remaining)) or '未知'}），停止失败。"
        )
    ok(f"端口 {port} 已释放（强杀）")


def stop_pid(pid, tokens, label, killed, failures) -> None:
    """按命令行判定并停止（Vite 这类进程没有 health 探针可用）。

    读不到命令行时宁可跳过：只靠 pid 数字无法证明归属，杀了就是误杀。
    """
    status, _cmdline = classify_pid(pid, tokens)
    if status == "gone":
        info(f"{label} pid {pid} 已不存在")
        return
    if status == "project":
        if kill_tree(pid):
            ok(f"已停止 {label}（pid {pid}）")
            killed.append((label, pid))
        else:
            failures.append(f"停止 {label}（pid {pid}）失败：权限不足或被保护，可用管理员权限重试。")
        return
    if status == "other":
        warn(f"pid {pid} 已被别的进程复用（命令行不是 {label}），跳过")
    else:
        warn(f"读不到 pid {pid} 的命令行，{label} 不能靠 health 探针判定，跳过（避免误杀）")


def runtime_pid_verdict(pid: int, port: int | None) -> tuple[str, str]:
    """判定 runtime 文件里的 pid 是否属于本项目，返回 (verdict, 说明)。

    verdict：
      gone       pid 已不存在；
      project    命令行可读且匹配（沿用原 token 校验）；
      probe      命令行读不到，但记录的端口通过 health 探针（不依赖系统权限）；
      reused     命令行可读但不像本项目（pid 被复用，绝不能杀）；
      unverified 读不到命令行且探针不确认，交给端口扫描。
    """
    status, _cmdline = classify_pid(pid, ("uvicorn", "app.main"))
    if status == "gone":
        return "gone", "已不存在"
    if status == "project":
        return "project", "命令行确认"
    if status == "other":
        return "reused", "命令行不是本项目（pid 可能已被复用）"
    if port is None:
        return "unverified", "读不到命令行，runtime 也没记录端口"
    ours, detail = probe_health(port)
    if ours:
        return "probe", f"端口 {port} 的 health 探针确认"
    return "unverified", f"读不到命令行，端口 {port} 的 health 探针不确认（{detail}）"


def cmd_stop(opts: Options) -> int:
    dry = opts.dry_run
    runtime = runtime_path(opts)
    state = load_runtime(runtime)
    if state is None and runtime.exists():
        warn(f"runtime 文件无效，按「没有状态文件」处理：{rel(runtime)}")

    server_pid = as_int(state.get("pid"), 1) if state else None
    vite_pid = as_int(state.get("vite_pid"), 1) if state else None
    known_port = as_int(state.get("port"), 1, 65535) if state else None

    step("停止 pgassistant" + ("（dry-run）" if dry else ""))
    if state is None:
        info(f"没有 runtime 文件（{rel(runtime)}），按端口兜底查找监听者")
    if opts.port is not None and not known_port:
        info(f"按 --port {opts.port} 兜底")

    killed = []
    failures = []
    planned = 0
    vite_killed = False
    summary_port = known_port or opts.port
    runtime_hit = False

    # 先停 Vite 再停后端：后端一退出，前台 launch.py 的 finally 会立刻清 Vite，
    # 两边同时 taskkill 同一棵树会让 stop 读不到命令行、把「Vite 已停」误报成「否」。
    if vite_pid:
        if dry:
            status, _cmdline = classify_pid(vite_pid, ("vite", "npm", "node"))
            if status == "project":
                planned += 1
                info(f"将停止 Vite（pid {vite_pid}，含子进程树）")
            elif status == "gone":
                info(f"Vite pid {vite_pid} 已不存在")
            else:
                warn(f"pid {vite_pid} 不确认是本项目的 Vite（{status}），跳过（避免误杀）")
        else:
            before = len(killed)
            stop_pid(vite_pid, ("vite", "npm", "node"), "Vite", killed, failures)
            vite_killed = len(killed) > before

    # runtime 里的后端 pid：命令行可读就用命令行；读不到就靠该端口的 health 探针。
    # 探针也不认（或 pid 已被复用）就落到下面的端口扫描，绝不凭 pid 数字直接杀。
    if server_pid:
        verdict, detail = runtime_pid_verdict(server_pid, known_port)
        if verdict == "gone":
            info(f"uvicorn pid {server_pid} 已不存在，改用端口判定")
        elif verdict in ("project", "probe"):
            if dry:
                planned += 1
                info(f"将停止 uvicorn（pid {server_pid}，{detail}）")
                runtime_hit = True
            elif kill_tree(server_pid):
                ok(f"已停止 uvicorn（pid {server_pid}，{detail}）")
                killed.append(("uvicorn", server_pid))
                runtime_hit = True
                if known_port is not None:
                    try:
                        ensure_port_released(known_port)
                        ok(f"端口 {known_port} 已释放")
                    except LaunchError as exc:
                        failures.append(str(exc))
            else:
                warn(f"停止 uvicorn（pid {server_pid}）失败：权限不足或被保护，改用端口判定")
        elif verdict == "reused":
            warn(f"pid {server_pid} 已被别的进程复用（{detail}），跳过")
        else:
            warn(f"无法确认 pid {server_pid} 的归属（{detail}），跳过")

    # 端口兜底：端口 → pid 只来自 netstat -ano，归属只信 health 探针。
    # 覆盖 runtime 缺失、runtime 里的 pid 判定失败、或用户显式给了 --port 的情况。
    scan_ports: list[int] = []
    if opts.port is not None:
        scan_ports.append(opts.port)
    if state is None:
        if opts.port is None:
            scan_ports = list(range(PORT_START, PORT_END + 1))
    elif not runtime_hit:
        fallback = list(range(PORT_START, PORT_END + 1))
        if known_port is not None:
            fallback = [known_port] + [port for port in fallback if port != known_port]
        for port in fallback:
            if port not in scan_ports:
                scan_ports.append(port)

    for port in scan_ports:
        listener_pids = find_listener_pids(port)
        if listener_pids is None:
            warn("没找到 netstat / lsof / ss，无法把端口映射到进程；请手动确认端口已释放。")
            break
        if not listener_pids:
            if not dry:
                info(f"端口 {port} 没有监听，跳过")
            continue
        pid_text = "/".join(map(str, listener_pids))
        ours, detail = probe_health(port)
        if not ours:
            warn(f"端口 {port} 被 pid {pid_text} 占用，跳过：{detail}（认不出归属，不杀）")
            continue
        if dry:
            planned += 1
            info(f"端口 {port}：将停止 pid {pid_text}（{detail}）")
            continue
        for pid in listener_pids:
            if pid <= 0:
                continue
            if any(pid == killed_pid for _label, killed_pid in killed):
                continue
            if kill_tree(pid):
                ok(f"端口 {port}：已停止 pid {pid}（{detail}）")
                killed.append((f"端口 {port} 的 pgassistant", pid))
                if summary_port is None:
                    summary_port = port
            else:
                warn(f"端口 {port}：停止 pid {pid} 失败（权限不足或被保护），等待复核/强杀")
        try:
            ensure_port_released(port)
            if any(label.startswith(f"端口 {port}") for label, _pid in killed):
                ok(f"端口 {port} 已释放")
        except LaunchError as exc:
            failures.append(str(exc))

    if dry:
        if planned:
            info(f"dry-run：将停止 {planned} 个进程")
        else:
            info("当前没有在运行的实例")
        info(f"将删除 {rel(runtime)}（如果存在）")
        print("\ndry-run 完成：以上为将要执行的步骤，未产生任何改动。", flush=True)
        return 0

    if killed:
        # 有失败就保留 runtime 文件，下一次 stop 还能从它重试。
        if runtime.exists() and not failures:
            try:
                runtime.unlink()
            except OSError as exc:
                failures.append(f"删除 {rel(runtime)} 失败：{exc}")
            else:
                ok(f"已清理 {rel(runtime)}")
        print(
            f"\n已停止 {summary_port if summary_port is not None else '本机实例'} / "
            f"Vite: {'是' if vite_killed else '否'} / 进程数 {len(killed)}",
            flush=True,
        )
    elif failures:
        print("\n  停止未完成（原因见下），保留 runtime 文件以便重试", flush=True)
    else:
        print("\n  当前没有在运行的实例", flush=True)
        if runtime.exists():
            # 端口上没有可确认的本项目进程时，仍清掉过期的 runtime 文件，
            # 否则下次启动会继续拿一个死 pid 当线索。
            try:
                runtime.unlink()
                ok(f"已清理过期的 {rel(runtime)}")
            except OSError as exc:
                warn(f"删除 {rel(runtime)} 失败：{exc}")

    if failures:
        for message in failures:
            warn(message)
        warn(f"停止未完成（{len(failures)} 个问题），退出码非 0。")
        return 1
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
