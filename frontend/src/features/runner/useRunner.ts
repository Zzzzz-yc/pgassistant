/**
 * 运行状态机（需求文档 §20.3 / §20.6）。
 *
 * 状态：`idle → loading → running → done | error`，任何一步都可 `reset()` 回 `idle`
 * （抽屉关闭即 reset：清空输出与耗时，不记历史，§20.4 最后一条）。
 *
 * 隔离口径：
 * - **每次 run 新建一个 Worker，结束就 terminate**（不复用，避免变量状态污染）；
 * - 5 秒没跑完 → terminate，报「运行超过 5 秒已终止（可能是死循环）」；
 * - 输出 100 KB 截断（Worker 侧裁一次，主线程再兜一层）；
 * - 运行时未缓存且离线 → 「运行时未就绪，请联网完成首次加载」+ 可重试；
 * - 耗时取 `performance.now()`，内存能取则取，取不到为 `null`（UI 显示「—」）。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ensurePyodideCached, isPyodideCached, PYODIDE_BASE } from "./pyodideCache";
import {
  decodeMessage,
  encodeMessage,
  OUTPUT_LIMIT_BYTES,
  type HostToWorker,
  type RunnerLang,
  type WorkerToHost,
} from "./protocol";
// `?worker&inline`：worker 以 base64 内联进主包，运行时**不需要再取一次脚本**。
// 这样断网时 JS 运行也能起来、Python 的 13 MB 资产才真正只依赖 CacheStorage。
import RunnerWorker from "./worker.ts?worker&inline";

/** 与 §20.3 一致：5 秒。 */
export const RUN_TIMEOUT_MS = 5000;
/** 首次加载 Pyodide（13 MB）比运行慢得多，单独给一个宽松上限。 */
export const LOAD_TIMEOUT_MS = 120_000;

export type RunnerStatus = "idle" | "loading" | "running" | "done" | "error";

export type RunnerErrorKind =
  | "timeout"
  | "runtime"
  | "syntax"
  | "memory"
  | "load"
  | "runtime-missing"
  | "unsupported";

export interface RunnerErrorState {
  kind: RunnerErrorKind;
  message: string;
  traceback?: string;
  line?: number;
}

export interface RunnerSnapshot {
  status: RunnerStatus;
  lang: RunnerLang | null;
  stdout: string;
  stderr: string;
  traceback: string | null;
  error: RunnerErrorState | null;
  elapsedMs: number | null;
  memoryBytes: number | null;
  truncated: boolean;
  /** loading 阶段的文案（加载运行时 / 预热缓存）。 */
  loadingText: string | null;
}

export const EMPTY_RUNNER_SNAPSHOT: RunnerSnapshot = {
  status: "idle",
  lang: null,
  stdout: "",
  stderr: "",
  traceback: null,
  error: null,
  elapsedMs: null,
  memoryBytes: null,
  truncated: false,
  loadingText: null,
};

/** 主线程侧的兜底截断（Worker 已经裁过，这里防止消息被拼爆）。 */
function cap(text: string): string {
  if (text.length <= OUTPUT_LIMIT_BYTES) return text;
  return text.slice(0, OUTPUT_LIMIT_BYTES);
}

function loadFailureMessage(reason: string): string {
  return (
    `Pyodide 加载失败：${reason}。` +
    "排查建议：① 确认前端产物里存在 /assets/pyodide/（开发态跑过一次 npm install）；" +
    "② 刷新页面重试；③ 浏览器若禁用了 WebAssembly 或 Worker，站内运行不可用。"
  );
}

/** 沙箱本身没起来（与 Pyodide 资产无关：JS 运行也会走到这里）。 */
function workerBootFailure(reason: string): string {
  return (
    `无法启动运行沙箱（${reason || "Worker 初始化异常"}）。` +
    "可能处于离线状态，或浏览器阻止了 Worker；请联网后重试。"
  );
}

/** traceback 里没有结构化行号时的兜底（Worker 侧已经解析过 `File "<exec>", line N`）。 */
function fallbackLineOf(traceback: string | undefined): number | undefined {
  if (!traceback) return undefined;
  const matches = [...traceback.matchAll(/line (\d+)/g)].map((match) => Number(match[1]));
  const valid = matches.filter((value) => Number.isFinite(value) && value > 0);
  return valid.length > 0 ? valid[valid.length - 1] : undefined;
}

export interface UseRunnerResult {
  snapshot: RunnerSnapshot;
  run: (lang: RunnerLang, code: string) => void;
  reset: () => void;
}

export function useRunner(): UseRunnerResult {
  const [snapshot, setSnapshot] = useState<RunnerSnapshot>(EMPTY_RUNNER_SNAPSHOT);

  const workerRef = useRef<Worker | null>(null);
  const loadTimerRef = useRef<number | null>(null);
  const runTimerRef = useRef<number | null>(null);
  /** 每次 run 自增；迟到的 worker 消息按代际丢弃。 */
  const generationRef = useRef(0);
  const disposedRef = useRef(false);

  const clearTimers = useCallback(() => {
    if (loadTimerRef.current !== null) {
      window.clearTimeout(loadTimerRef.current);
      loadTimerRef.current = null;
    }
    if (runTimerRef.current !== null) {
      window.clearTimeout(runTimerRef.current);
      runTimerRef.current = null;
    }
  }, []);

  const stopWorker = useCallback(() => {
    const worker = workerRef.current;
    workerRef.current = null;
    if (worker) {
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
    }
    clearTimers();
  }, [clearTimers]);

  const reset = useCallback(() => {
    generationRef.current += 1;
    stopWorker();
    setSnapshot(EMPTY_RUNNER_SNAPSHOT);
  }, [stopWorker]);

  useEffect(() => {
    // StrictMode 下 effect 会「挂载 → 清理 → 再挂载」：清理里置 true，这里必须显式复位，
    // 否则第二次挂载后 `spawn()` 会被 `disposedRef` 挡住，状态永远停在 loading。
    disposedRef.current = false;
    return () => {
      disposedRef.current = true;
      stopWorker();
    };
  }, [stopWorker]);

  const fail = useCallback(
    (generation: number, error: RunnerErrorState, extra?: Partial<RunnerSnapshot>) => {
      if (generation !== generationRef.current) return;
      stopWorker();
      setSnapshot((prev) => ({
        ...prev,
        ...extra,
        status: "error",
        error,
        loadingText: null,
        traceback: error.traceback ?? prev.traceback,
      }));
    },
    [stopWorker],
  );

  const run = useCallback(
    (lang: RunnerLang, code: string) => {
      generationRef.current += 1;
      const generation = generationRef.current;
      stopWorker();

      setSnapshot({
        ...EMPTY_RUNNER_SNAPSHOT,
        status: "loading",
        lang,
        loadingText: lang === "python" ? "正在准备 Python 运行时…" : "正在准备沙箱…",
      });

      const spawn = () => {
        if (generation !== generationRef.current || disposedRef.current) return;

        let worker: Worker;
        try {
          // 内联 Worker：脚本随主包走，不产生额外网络请求（离线可用）。
          // 代价是 Worker 的 `location` 是 blob:，所以 Pyodide 的 indexURL 由主线程算成绝对
          // 地址从 `init` 消息里传进去（见 worker.ts 的 pyodideBase）。
          worker = new RunnerWorker();
        } catch (error) {
          fail(generation, {
            kind: "load",
            message: workerBootFailure(String((error as Error)?.message ?? error)),
          });
          return;
        }

        workerRef.current = worker;

        worker.onmessage = (event: MessageEvent) => {
          if (generation !== generationRef.current) return;
          const message = decodeMessage<WorkerToHost>(event.data);
          if (!message) return;

          if (message.type === "ready") {
            if (loadTimerRef.current !== null) {
              window.clearTimeout(loadTimerRef.current);
              loadTimerRef.current = null;
            }
            setSnapshot((prev) => ({ ...prev, status: "running", loadingText: null }));

            // 运行计时从「真正开始跑」起算：加载 Pyodide 的时间不算进 5 秒
            const out: HostToWorker = { type: "run", code };
            worker.postMessage(encodeMessage(out));
            runTimerRef.current = window.setTimeout(() => {
              fail(
                generation,
                { kind: "timeout", message: "运行超过 5 秒已终止（可能是死循环）" },
                { elapsedMs: RUN_TIMEOUT_MS },
              );
            }, RUN_TIMEOUT_MS);
            return;
          }

          if (message.type === "chunk") {
            setSnapshot((prev) =>
              message.stream === "stdout"
                ? { ...prev, stdout: cap(prev.stdout + message.text) }
                : { ...prev, stderr: cap(prev.stderr + message.text) },
            );
            return;
          }

          if (message.type === "notice") {
            if (message.code === "output-truncated") {
              setSnapshot((prev) => ({ ...prev, truncated: true }));
            }
            return;
          }

          if (message.type === "done") {
            if (runTimerRef.current !== null) {
              window.clearTimeout(runTimerRef.current);
              runTimerRef.current = null;
            }
            stopWorker();
            setSnapshot((prev) => ({
              ...prev,
              status: "done",
              elapsedMs: message.ms,
              memoryBytes: message.memoryBytes,
            }));
            return;
          }

          // message.type === "error"
          if (runTimerRef.current !== null) {
            window.clearTimeout(runTimerRef.current);
            runTimerRef.current = null;
          }
          const isLoad = message.kind === "load";
          fail(
            generation,
            {
              kind: isLoad ? "load" : message.kind === "syntax" ? "syntax" : "runtime",
              message: isLoad ? loadFailureMessage(message.message) : message.message,
              traceback: isLoad ? undefined : message.traceback,
              line: message.line ?? fallbackLineOf(message.traceback),
            },
            { elapsedMs: message.ms ?? null },
          );
        };

        worker.onerror = (event: ErrorEvent) => {
          if (generation !== generationRef.current) return;
          fail(generation, {
            kind: "load",
            message: workerBootFailure(event.message),
          });
        };

        loadTimerRef.current = window.setTimeout(() => {
          fail(generation, {
            kind: "load",
            message: loadFailureMessage("加载超时（120 秒内没有完成初始化）"),
          });
        }, LOAD_TIMEOUT_MS);

        const init: HostToWorker = {
          type: "init",
          lang,
          baseUrl: new URL(PYODIDE_BASE, window.location.href).href,
        };
        worker.postMessage(encodeMessage(init));
      };

      if (lang !== "python") {
        spawn();
        return;
      }

      // Python：先确认运行时缓存，再决定是预热还是直接给离线提示
      void (async () => {
        const cached = await isPyodideCached();
        if (generation !== generationRef.current) return;

        if (!cached) {
          if (typeof navigator !== "undefined" && navigator.onLine === false) {
            fail(generation, {
              kind: "runtime-missing",
              message: "运行时未就绪，请联网完成首次加载",
            });
            return;
          }
          setSnapshot((prev) => ({
            ...prev,
            loadingText: "正在准备 Python 运行时（约 13 MB，仅首次需要）",
          }));
          const ok = await ensurePyodideCached((done, total) => {
            if (generation !== generationRef.current) return;
            setSnapshot((prev) => ({
              ...prev,
              loadingText: `正在准备 Python 运行时（${done}/${total}，仅首次需要）`,
            }));
          });
          if (generation !== generationRef.current) return;
          if (!ok) {
            fail(generation, {
              kind: "runtime-missing",
              message: "运行时未就绪，请联网完成首次加载",
            });
            return;
          }
        }
        spawn();
      })();
    },
    [fail, stopWorker],
  );

  return { snapshot, run, reset };
}
