/**
 * Worker ↔ 主线程的消息协议（需求文档 §20.5 第 1 条：只通过 postMessage 传递字符串）。
 *
 * **所有消息都序列化成 JSON 字符串再 postMessage**：不传对象引用、不传函数、不传 ArrayBuffer，
 * 主线程侧 `JSON.parse` 后按 `type` 分流。这样沙箱里哪怕拿到一个畸形消息也只能崩自己那一帧。
 */

/** 站内可运行的语言（§20.1：只有 `runnable` 列表里的语言出现运行按钮）。 */
export type RunnerLang = "python" | "javascript";

export type StreamName = "stdout" | "stderr";

export type HostToWorker =
  /**
   * `baseUrl` 是**绝对**的运行时目录（`http://host:port/assets/pyodide/`）。
   * Worker 是内联 Blob（见 `useRunner.ts`），`location` 不是 http URL，
   * Pyodide 内部的 `new URL(相对路径, location)` 会抛 Invalid URL——所以由主线程算好绝对地址传进来。
   */
  | { type: "init"; lang: RunnerLang; baseUrl: string }
  | { type: "run"; code: string };

export type WorkerErrorKind = "runtime" | "syntax" | "memory" | "load" | "too-large";

export type WorkerToHost =
  /** 运行时初始化完成（Python = Pyodide 已加载；JavaScript = 沙箱已就位）。 */
  | { type: "ready"; loadMs: number }
  /** 增量输出。主线程按 100 KB 上限再兜一层。 */
  | { type: "chunk"; stream: StreamName; text: string }
  /** 本次运行正常结束。 */
  | { type: "done"; ms: number; memoryBytes: number | null }
  /** 本次运行失败（traceback 原样带回来，渲染侧不解析、只做文本切分）。 */
  | {
      type: "error";
      kind: WorkerErrorKind;
      message: string;
      traceback?: string;
      line?: number;
      ms?: number;
    }
  /** 非致命提示（例如输出被截断）。 */
  | { type: "notice"; code: "output-truncated" };

export const OUTPUT_LIMIT_BYTES = 100 * 1024;

/** 把消息编码成字符串（worker 与主线程共用同一套键名，避免两边各写一半）。 */
export function encodeMessage(message: HostToWorker | WorkerToHost): string {
  return JSON.stringify(message);
}

export function decodeMessage<T>(raw: unknown): T | null {
  if (typeof raw !== "string") return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}
