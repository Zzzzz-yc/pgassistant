/**
 * 站内代码运行的执行器（需求文档 §20.3「执行与隔离」、§20.5「安全边界」）。
 *
 * **这是一个经典 Worker（不是 module worker）**，而且**不 import 任何运行时模块**：
 * 整个文件自包含，只有这样 Vite 在 dev 与 build 两种模式下都能把它当作经典脚本发出去，
 * `importScripts` / 间接 `eval` 这类经典 Worker 的手段才可用（Vite 的 `worker.format`
 * 属前端骨架的 `vite.config.ts`，本模块不能改它）。
 *
 * 两条链路：
 * - **Python**：从 `/assets/pyodide/` 加载本地 Pyodide（不走 CDN），
 *   `fetch` 先查 CacheStorage（`pyodideCache.ts` 写的桶），离线可用；
 * - **JavaScript**：`new Function` + `with` 作用域代理屏蔽 window/document/localStorage/
 *   fetch/XHR/importScripts，同时把这些全局从 Worker 全局对象上摘掉（双保险）。
 *
 * 隔离口径（§20.3）：每次运行一个独立 Worker，主线程 5 秒超时后 terminate；
 * 输出 100 KB 封顶并提示截断；无网络（fetch/XHR/WebSocket/pyodide.http/socket 全部拒绝）；
 * 无磁盘（Pyodide 用 MEMFS，随 Worker 销毁）。
 *
 * 与主线程只交换 **JSON 字符串**（§20.5-1），协议见 `protocol.ts`。
 */
import type { RunnerLang, StreamName, WorkerToHost } from "./protocol";

/* ------------------------------------------------------------------ 常量 */

/** 与 `protocol.ts` 的 `OUTPUT_LIMIT_BYTES` 同值；本文件自包含，故此处再写一遍。 */
const OUTPUT_LIMIT = 100 * 1024;

/** 与 `pyodideCache.ts` 的常量保持一致（同样为了自包含）。 */
const PYODIDE_CACHE_NAME = "pgassistant-pyodide-v1";

/**
 * 运行时目录的**绝对** URL，由主线程在 `init` 消息里给（Worker 是内联 Blob，
 * 自己的 `location` 是 `blob:`，拿不到可用的 http 基准）。默认值只作兜底。
 */
let pyodideBase = "/assets/pyodide/";

/** JS 包装器：第 1 行是包装头，用户代码从第 2 行开始，所以栈里的行号要减 1。 */
const JS_WRAPPER_HEAD = "with (__pgScope) { (function () {\n";
const JS_WRAPPER_TAIL = "\n}).call(undefined); }";
const JS_LINE_OFFSET = 1;

/** 从全局对象上摘掉的名字（§20.5-3 + §20.3「无网络」）。 */
const BLOCKED_GLOBALS = [
  "fetch",
  "XMLHttpRequest",
  "XMLHttpResponse",
  "WebSocket",
  "EventSource",
  "importScripts",
  "indexedDB",
];

/** 用户代码里被 `with` 作用域拦下的标识符（比只删全局更早失败、报错更直白）。 */
const BLOCKED_IDENTIFIERS = [
  ...BLOCKED_GLOBALS,
  "window",
  "document",
  "localStorage",
  "sessionStorage",
  "top",
  "parent",
  "opener",
  "globalThis",
  "self",
  "require",
  "process",
];

/* -------------------------------------------------------------- 作用域接口 */

interface WorkerScope {
  postMessage(message: string): void;
  addEventListener(type: "message", listener: (event: MessageEvent) => void): void;
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
  location: { href: string };
  importScripts?: (url: string) => void;
  performance: Performance;
}

interface PyodideApi {
  runPythonAsync(code: string): Promise<unknown>;
  runPython(code: string): unknown;
  version?: string;
  _module?: { HEAP8?: { length: number } };
}

const scope = globalThis as unknown as WorkerScope;

/* ------------------------------------------------------------------ 消息 */

function send(message: WorkerToHost): void {
  scope.postMessage(JSON.stringify(message));
}

/* ------------------------------------------------------------ 输出与截断 */

const encoder = new TextEncoder();
let usedBytes = 0;
let truncatedNotified = false;

/**
 * 补行尾换行（验收 P1）：Pyodide 0.26 的 batched 回调按「行」给文本且**不含 `\n`**
 * （`print('a')` 只收到 `"a"`），直接 emit 会把多行输出粘成 `ab`。
 * 已经以换行结尾的原样返回，避免重复补。
 */
function asLine(text: string): string {
  if (!text) return "";
  return text.endsWith("\n") ? text : `${text}\n`;
}

function markTruncated(): void {
  if (truncatedNotified) return;
  truncatedNotified = true;
  send({ type: "notice", code: "output-truncated" });
}

/** 输出累加到 100 KB 就截断（宁可少给几字节，不切成半个多字节字符）。 */
function emit(stream: StreamName, text: string): void {
  if (!text) return;
  if (usedBytes >= OUTPUT_LIMIT) {
    markTruncated();
    return;
  }
  const remain = OUTPUT_LIMIT - usedBytes;
  let piece = text;
  if (encoder.encode(piece).length > remain) {
    let keep = piece.length;
    while (keep > 0 && encoder.encode(piece.slice(0, keep)).length > remain) {
      keep = Math.floor(keep * 0.9);
    }
    piece = piece.slice(0, keep);
    markTruncated();
  }
  usedBytes += encoder.encode(piece).length;
  if (piece) send({ type: "chunk", stream, text: piece });
}

/* --------------------------------------------------------- 缓存优先 fetch */

let cacheFetchInstalled = false;

/**
 * 装一层「CacheStorage 优先」的 fetch：命中缓存直接返回（离线可用），未命中走网络并把
 * 响应回填缓存（下次离线可用）。Pyodide 内部的 wasm / stdlib / lock 请求都从这里过。
 */
function installCacheFirstFetch(): void {
  if (cacheFetchInstalled) return;
  cacheFetchInstalled = true;

  const realFetch = scope.fetch.bind(globalThis);
  const patched = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    let url = "";
    try {
      url =
        typeof input === "string"
          ? new URL(input, scope.location.href).href
          : typeof input === "object" && input !== null && "url" in input
            ? String((input as { url: string }).url)
            : String(input);
    } catch {
      url = String(input);
    }

    if (url && typeof caches !== "undefined") {
      try {
        const cache = await caches.open(PYODIDE_CACHE_NAME);
        const hit = await cache.match(url);
        if (hit) return hit;
      } catch {
        // 缓存不可用（隐私模式）就当没命中，继续走网络
      }
    }

    const response = await realFetch(input, init);
    if (url && response.ok && typeof caches !== "undefined") {
      try {
        const clone = response.clone();
        void caches
          .open(PYODIDE_CACHE_NAME)
          .then((cache) => cache.put(url, clone))
          .catch(() => undefined);
      } catch {
        // clone 失败（body 已被消费）不影响本次返回
      }
    }
    return response;
  };

  Object.defineProperty(globalThis, "fetch", {
    value: patched,
    configurable: true,
    writable: true,
  });
}

/* --------------------------------------------------------------- 加载脚本 */

/**
 * 用间接 `eval` 在 Worker 全局作用域执行一个经典脚本（与 `importScripts` 等价），
 * 但正文是先经上面的缓存优先 fetch 取的，所以离线也能用。失败再退回 `importScripts`。
 */
async function evalScript(url: string): Promise<void> {
  try {
    const response = await scope.fetch(url);
    if (!response.ok) throw new Error(`HTTP ${response.status} ${url}`);
    const text = await response.text();
    (0, eval)(text);
  } catch (error) {
    if (typeof scope.importScripts === "function") {
      scope.importScripts(url);
      return;
    }
    throw error;
  }
}

/* --------------------------------------------------------------- 网络封禁 */

let globalsBlocked = false;

/** 把危险全局替换成「一碰就抛」。Worker 用完即弃，不需要恢复。 */
function blockGlobals(): void {
  if (globalsBlocked) return;
  globalsBlocked = true;
  for (const key of BLOCKED_GLOBALS) {
    const blocked = () => {
      throw new Error(`站内运行沙箱已禁用 ${key}()：代码在你本机的沙箱内运行，不联网`);
    };
    try {
      Object.defineProperty(globalThis, key, {
        value: blocked,
        configurable: false,
        writable: false,
      });
    } catch {
      // 个别属性不可重定义：sandbox 的 with 作用域仍会拦下它
    }
  }
}

/* ------------------------------------------------------------------ Python */

let pyodide: PyodideApi | null = null;

/** Python 侧的网络封禁：socket 与 pyodide.http 都换成抛错函数（§20.3「无网络」）。 */
const PYTHON_NETWORK_BLOCK = `
def _pg_blocked_network(*_args, **_kwargs):
    raise OSError("站内运行沙箱已禁用网络访问")

try:
    import socket
    socket.socket = _pg_blocked_network
    socket.create_connection = _pg_blocked_network
except Exception:
    pass

try:
    import pyodide.http as _pg_http
    _pg_http.pyfetch = _pg_blocked_network
    _pg_http.open_url = _pg_blocked_network
except Exception:
    pass
`;

async function initPython(): Promise<void> {
  installCacheFirstFetch();

  const startedAt = scope.performance.now();
  await evalScript(pyodideBase + "pyodide.js");

  const loadPyodide = (globalThis as unknown as { loadPyodide?: unknown }).loadPyodide;
  if (typeof loadPyodide !== "function") {
    throw new Error("Pyodide 加载器没有就位（/assets/pyodide/pyodide.js 可能不存在）");
  }

  // 先把 emscripten 胶水层装好：loadPyodide 会绕开 importScripts，从而走我们的缓存
  await evalScript(pyodideBase + "pyodide.asm.js");
  if (typeof (globalThis as unknown as Record<string, unknown>)._createPyodideModule !== "function") {
    if (typeof scope.importScripts === "function") {
      scope.importScripts(pyodideBase + "pyodide.asm.js");
    }
  }

  const loader = loadPyodide as (options: Record<string, unknown>) => Promise<PyodideApi>;
  pyodide = await loader({
    indexURL: pyodideBase,
    // Pyodide 0.26 的 batched 回调给的是「行文本、不含换行」（`print('a')` 只收到 `"a"`），
    // 直接 emit 会把多行输出粘成一行；这里按行补齐（已有换行的不重复补）。
    stdout: (text: string) => emit("stdout", asLine(text)),
    stderr: (text: string) => emit("stderr", asLine(text)),
  });

  blockGlobals();
  try {
    pyodide.runPython(PYTHON_NETWORK_BLOCK);
  } catch {
    // 封禁脚本本身失败不阻断运行：JS 层 fetch 已封
  }

  send({ type: "ready", loadMs: scope.performance.now() - startedAt });
}

/** Pyodide 堆占用（能取到就报，取不到给 null，UI 显示「—」）。 */
function pythonMemory(): number | null {
  try {
    const bytes = pyodide?._module?.HEAP8?.length;
    return typeof bytes === "number" && bytes > 0 ? bytes : null;
  } catch {
    return null;
  }
}

/** 从 traceback 里取用户代码行号（Pyodide 的 `<exec>` 帧就是用户代码）。 */
function pythonLineOf(traceback: string): number | undefined {
  const execLines = [...traceback.matchAll(/File "<exec>", line (\d+)/g)].map((match) =>
    Number(match[1]),
  );
  const valid = execLines.filter((value) => Number.isFinite(value) && value > 0);
  return valid.length > 0 ? valid[valid.length - 1] : undefined;
}

/**
 * 去掉 Pyodide 自己的调用栈帧（`/lib/python3xx.zip/_pyodide/_base.py` 的 `eval_code_async`
 * 那两帧）。留着它们会让 traceback 里的「行号」变成 597 / 411 这种内部行，点一下就跳到用户
 * 代码里完全没有关系的位置——用户只应该看到并点击 `<exec>` 帧。
 */
function stripInternalFrames(traceback: string): string {
  const lines = traceback.split("\n");
  const out: string[] = [];
  let skipping = false;

  for (const line of lines) {
    if (/^\s*File ".*_pyodide\//.test(line)) {
      skipping = true;
      continue;
    }
    if (skipping) {
      if (/^\s*File "/.test(line)) {
        // 下一帧（例如 `<exec>`）要保留
        skipping = false;
      } else if (/^\s{4,}/.test(line) || /^\s*\^+\s*$/.test(line) || line.trim() === "") {
        // 内部帧的源码行、脱字符行、空行一起丢掉
        continue;
      } else {
        skipping = false;
      }
    }
    out.push(line);
  }
  return out.join("\n");
}

async function runPython(code: string): Promise<void> {
  if (!pyodide) {
    send({ type: "error", kind: "load", message: "Python 运行时未就绪" });
    return;
  }
  usedBytes = 0;
  truncatedNotified = false;
  const startedAt = scope.performance.now();

  try {
    await pyodide.runPythonAsync(code);
    send({
      type: "done",
      ms: scope.performance.now() - startedAt,
      memoryBytes: pythonMemory(),
    });
  } catch (error) {
    const raw = String((error as { message?: string })?.message ?? error);
    const traceback = stripInternalFrames(raw);
    send({
      type: "error",
      kind: "runtime",
      message: lastLine(traceback) || "运行失败",
      traceback,
      line: pythonLineOf(traceback),
      ms: scope.performance.now() - startedAt,
    });
  }
}

/* -------------------------------------------------------------- JavaScript */

/** 拦截用的代理：读属性、调用都直接抛，避免用户以为「拿到了但没效果」。 */
function blockedIdentifier(name: string): unknown {
  const fail = () => {
    throw new Error(
      `站内运行沙箱已禁用 ${name}：代码在你本机的沙箱内运行，不能访问浏览器或网络能力`,
    );
  };
  return new Proxy(function blocked() {}, {
    get: fail,
    apply: fail,
    construct: fail,
  });
}

const blockedCache = new Map<string, unknown>();

function makeScopeProxy(overrides: Record<string, unknown> = {}): object {
  const reader = (key: string): unknown => {
    if (Object.prototype.hasOwnProperty.call(overrides, key)) return overrides[key];
    if (blockedCache.has(key)) return blockedCache.get(key);
    if (BLOCKED_IDENTIFIERS.includes(key)) {
      const value = blockedIdentifier(key);
      blockedCache.set(key, value);
      return value;
    }
    return (globalThis as unknown as Record<string, unknown>)[key];
  };

  return new Proxy(Object.create(null) as Record<string, unknown>, {
    // 让 `with` 把**所有**裸标识符都先问一遍这个作用域：既拦危险名，又保证 Math/JSON 等照常可用
    has: () => true,
    get: (_target, key) => {
      if (typeof key === "symbol") return undefined;
      return reader(key);
    },
    set: () => true,
  });
}

/** 极简 console 实现：全部进 stdout/stderr，不碰真正的主线程控制台。 */
function makeConsole(stream: (text: string) => void) {
  const format = (values: unknown[]): string =>
    values
      .map((value) => {
        if (typeof value === "string") return value;
        if (typeof value === "object" && value !== null) {
          try {
            const json = JSON.stringify(value);
            return json === undefined ? String(value) : json;
          } catch {
            return String(value);
          }
        }
        return String(value);
      })
      .join(" ");

  return {
    log: (...values: unknown[]) => stream(`${format(values)}\n`),
    info: (...values: unknown[]) => stream(`${format(values)}\n`),
    debug: (...values: unknown[]) => stream(`${format(values)}\n`),
    warn: (...values: unknown[]) => stream(`${format(values)}\n`),
    error: (...values: unknown[]) => stream(`${format(values)}\n`),
    table: (...values: unknown[]) => stream(`${format(values)}\n`),
  };
}

function toUserLine(reported: number | undefined): number | undefined {
  if (!reported || !Number.isFinite(reported)) return undefined;
  const line = reported - JS_LINE_OFFSET;
  return line > 0 ? line : undefined;
}

/** 把 V8 的 `<anonymous>:N:M` 帧翻成「第 N-1 行」，只留用户代码那几帧。 */
function jsTraceback(error: Error): { traceback: string; line?: number } {
  const stack = typeof error.stack === "string" ? error.stack : "";
  const frames = stack.split("\n").slice(0, 8);
  const mapped: string[] = [];
  let firstLine: number | undefined;

  for (const frame of frames) {
    const match = /<anonymous>:(\d+):(\d+)/.exec(frame);
    if (!match) continue;
    const line = toUserLine(Number(match[1]));
    if (!line) continue;
    if (firstLine === undefined) firstLine = line;
    mapped.push(`  at 第 ${line} 行`);
  }

  const head = `${error.name || "Error"}: ${error.message || "运行失败"}`;
  return {
    traceback: mapped.length > 0 ? `${head}\n${mapped.join("\n")}` : head,
    line: firstLine,
  };
}

function runJavaScript(code: string): void {
  usedBytes = 0;
  truncatedNotified = false;
  const startedAt = scope.performance.now();

  let fn: (probe: object) => void;
  try {
    // 注意：`new Function` 的函数体是**非严格模式**，只有非严格模式才能用 `with`
    fn = new Function("__pgScope", JS_WRAPPER_HEAD + code + JS_WRAPPER_TAIL) as (
      probe: object,
    ) => void;
  } catch (error) {
    const syntaxError = error as Error;
    const { traceback, line } = jsTraceback(syntaxError);
    send({
      type: "error",
      kind: "syntax",
      message: firstLine(`${syntaxError.name}: ${syntaxError.message}`),
      traceback,
      line,
      ms: scope.performance.now() - startedAt,
    });
    return;
  }

  const sandboxConsole = makeConsole((text) => emit("stdout", text));
  const sandboxConsoleError = makeConsole((text) => emit("stderr", text));
  const consoleShim = {
    log: sandboxConsole.log,
    info: sandboxConsole.info,
    debug: sandboxConsole.debug,
    table: sandboxConsole.table,
    warn: sandboxConsoleError.warn,
    error: sandboxConsoleError.error,
  };

  try {
    fn(makeScopeProxy({ console: consoleShim }));
    send({
      type: "done",
      ms: scope.performance.now() - startedAt,
      memoryBytes: jsMemory(),
    });
  } catch (error) {
    const runtimeError = error instanceof Error ? error : new Error(String(error));
    // Python/JS 两侧共用的「沙箱已禁用 xxx」不是用户代码的错，单独给 kind
    const blocked = /站内运行沙箱已禁用/.test(runtimeError.message);
    const { traceback, line } = jsTraceback(runtimeError);
    send({
      type: "error",
      kind: "runtime",
      message: blocked ? runtimeError.message : firstLine(traceback),
      traceback: blocked ? runtimeError.message : traceback,
      line,
      ms: scope.performance.now() - startedAt,
    });
  }
}

function jsMemory(): number | null {
  try {
    const memory = (scope.performance as unknown as { memory?: { usedJSHeapSize?: number } }).memory;
    const used = memory?.usedJSHeapSize;
    return typeof used === "number" && used > 0 ? used : null;
  } catch {
    return null;
  }
}

/* -------------------------------------------------------------------- 调度 */

function firstLine(text: string): string {
  const line = text.split("\n").find((item) => item.trim().length > 0);
  return (line ?? "").trim();
}

/** traceback 的最后一行是异常摘要（`ZeroDivisionError: division by zero`）。 */
function lastLine(text: string): string {
  const lines = text.split("\n");
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index].trim();
    if (line) return line;
  }
  return "";
}

let lang: RunnerLang | null = null;
let ready = false;
let busy = false;

async function handleInit(next: RunnerLang, baseUrl?: string): Promise<void> {
  if (lang !== null) return;
  lang = next;
  if (typeof baseUrl === "string" && baseUrl) {
    // 主线程给的绝对地址（内联 Worker 的 location 是 blob:，无法作 URL 基准）
    pyodideBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  }
  try {
    if (next === "python") {
      await initPython();
    } else {
      blockGlobals();
      send({ type: "ready", loadMs: 0 });
    }
    ready = true;
  } catch (error) {
    const message = String((error as { message?: string })?.message ?? error);
    send({ type: "error", kind: "load", message });
  }
}

async function handleRun(code: string): Promise<void> {
  if (busy) return;
  if (!ready) {
    send({ type: "error", kind: "load", message: "运行时尚未就绪" });
    return;
  }
  busy = true;
  if (lang === "python") await runPython(code);
  else runJavaScript(code);
}

scope.addEventListener("message", (event) => {
  let message: { type?: string; lang?: RunnerLang; code?: string; baseUrl?: string } | null = null;
  try {
    message = typeof event.data === "string" ? JSON.parse(event.data) : null;
  } catch {
    message = null;
  }
  if (!message || typeof message.type !== "string") return;
  if (message.type === "init" && (message.lang === "python" || message.lang === "javascript")) {
    void handleInit(message.lang, message.baseUrl);
    return;
  }
  if (message.type === "run" && typeof message.code === "string") {
    void handleRun(message.code);
  }
});
