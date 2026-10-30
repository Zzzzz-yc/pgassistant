/**
 * 唯一传输层（docs/contracts/MODULES.md §3.2 / API.md §1.2）。
 *
 * 职责边界：
 * - 注入 `X-App-Token`（令牌只能来自 `<meta name="x-app-token">`，禁止 query / Cookie / localStorage）；
 * - 解析统一错误信封 `{code, message, detail?, hint?, request_id?}` 成 `ApiError`；
 * - 403 `TOKEN_INVALID` 时派发 `app:reload-required`，由布局层弹「服务已重启，请重新加载页面」；
 * - 提供 SSE 读取（`EventSource` 带不了自定义头，必须 fetch + ReadableStream）。
 *
 * 业务接口函数不放这里：它们按域放 `lib/api/{resources,plans,agent,settings,index}.ts`。
 */
import type { ErrorEnvelope } from "@/types/api";

export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

/** 401/403 之外也可能是网络错误：`status = 0` 表示请求没到达服务端。 */
export const NETWORK_ERROR = "NETWORK_ERROR";
/** 前端本地码：SSE 帧不是合法 JSON（不会由后端产生）。 */
export const SSE_PARSE_ERROR = "SSE_PARSE_ERROR";

/** 令牌失效时派发的事件名；监听方负责提示并引导硬刷新。 */
export const RELOAD_REQUIRED_EVENT = "app:reload-required";

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  /** JSON 请求体；`undefined` 表示不带 body（也就不带 Content-Type）。 */
  body?: unknown;
  /** query 参数；`undefined` / `null` / `""` 会被丢掉。 */
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  /** 额外请求头（业务模块一般不需要）。 */
  headers?: Record<string, string>;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail?: unknown;
  readonly hint?: string;
  readonly requestId?: string;

  constructor(
    code: string,
    message: string,
    status: number,
    extra: { detail?: unknown; hint?: string; requestId?: string } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = extra.detail;
    this.hint = extra.hint;
    this.requestId = extra.requestId;
  }

  /** 令牌过期（服务重启）——UI 必须提示重新加载页面（§4.4 第 4 条）。 */
  get isTokenInvalid(): boolean {
    return this.code === "TOKEN_INVALID" || this.code === "TOKEN_MISSING";
  }
}

/**
 * 读 `<meta name="x-app-token">`。
 * 拿不到返回空串：请求会 403 TOKEN_MISSING，页面提示从启动器重新打开。
 */
export function appToken(): string {
  if (typeof document === "undefined") return "";
  const meta = document.querySelector<HTMLMetaElement>('meta[name="x-app-token"]');
  return meta?.content?.trim() ?? "";
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  if (!qs) return path;
  return path.includes("?") ? `${path}&${qs}` : `${path}?${qs}`;
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = appToken();
  if (token) headers["X-App-Token"] = token;
  if (extra) Object.assign(headers, extra);
  return headers;
}

function notifyReloadRequired(error: ApiError): void {
  if (typeof window === "undefined") return;
  if (!error.isTokenInvalid) return;
  window.dispatchEvent(
    new CustomEvent(RELOAD_REQUIRED_EVENT, {
      detail: { code: error.code, message: error.message },
    }),
  );
}

async function toApiError(response: Response): Promise<ApiError> {
  const status = response.status;
  const text = await response.text().catch(() => "");
  let envelope: Partial<ErrorEnvelope> | null = null;
  if (text) {
    try {
      envelope = JSON.parse(text) as Partial<ErrorEnvelope>;
    } catch {
      envelope = null;
    }
  }
  const code = envelope?.code?.trim() || (status === 0 ? NETWORK_ERROR : `HTTP_${status}`);
  const message = envelope?.message?.trim() || `请求失败（HTTP ${status}）`;
  return new ApiError(code, message, status, {
    detail: envelope?.detail,
    hint: envelope?.hint ?? undefined,
    requestId: envelope?.request_id ?? undefined,
  });
}

/**
 * 发一个 JSON 请求并返回解析后的 body。
 *
 * 失败一律抛 `ApiError`（含网络失败：`status = 0`、`code = NETWORK_ERROR`），
 * 调用方只按 `error.code` 分支，不解析 `message`。
 */
export async function request<T>(
  method: HttpMethod,
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const url = buildUrl(path, opts.query);
  const headers = authHeaders(opts.headers);
  const init: RequestInit = {
    method,
    headers,
    signal: opts.signal,
    credentials: "same-origin",
  };
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json; charset=utf-8";
    init.body = JSON.stringify(opts.body);
  }

  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(NETWORK_ERROR, "连不上本地服务", 0, { detail: String(cause) });
  }

  if (!response.ok) {
    const error = await toApiError(response);
    notifyReloadRequired(error);
    throw error;
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch (cause) {
    throw new ApiError(SSE_PARSE_ERROR, "服务端返回的不是 JSON", response.status, {
      detail: String(cause),
    });
  }
}

/* ------------------------------------------------------------------ SSE */

export type SseEventName = "intent" | "delta" | "done" | "error";

export interface SSEHandlers {
  /** 每个事件都会调一次（M4 用它做通用日志/调试）。 */
  onEvent?: (event: SseEventName | string, data: unknown) => void;
  onIntent?: (data: unknown) => void;
  onDelta?: (data: unknown) => void;
  onDone?: (data: unknown) => void;
  onError?: (error: ApiError) => void;
}

function isAbortError(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === "AbortError";
}

function dispatchFrame(
  frame: string,
  handlers: SSEHandlers,
  handlersByName: Map<string, (data: unknown) => void>,
): "continue" | "stop" {
  let eventName = "message";
  const dataLines: string[] = [];

  for (const rawLine of frame.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line || line.startsWith(":")) continue; // 注释行（心跳 ": ping"）直接忽略
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
  }

  if (dataLines.length === 0) return "continue";
  const payloadText = dataLines.join("\n");
  let payload: unknown;
  try {
    payload = JSON.parse(payloadText);
  } catch (cause) {
    const error = new ApiError(SSE_PARSE_ERROR, "流式响应解析失败", 200, {
      detail: { payload: payloadText, cause: String(cause) },
    });
    handlers.onError?.(error);
    return "stop";
  }

  handlers.onEvent?.(eventName, payload);
  const byName = handlersByName.get(eventName);
  if (byName) byName(payload);
  if (eventName === "error") return "stop";
  if (eventName === "done") return "stop";
  return "continue";
}

/**
 * 读 SSE（`POST` + JSON body）。
 *
 * - 流开始前失败（鉴权/限流/参数）时后端返回普通 JSON 信封：这里抛 `ApiError`；
 * - 流内 `event: error` 交给 `handlers.onError`，随后立即结束；
 * - `signal` 中止时抛 `AbortError`（调用方按 `signal.aborted` 判断，不要当错误上报）。
 */
export async function readSSE(
  path: string,
  body: unknown,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers = authHeaders({
    "Content-Type": "application/json; charset=utf-8",
    Accept: "text/event-stream",
  });

  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal,
      credentials: "same-origin",
    });
  } catch (cause) {
    if (isAbortError(cause)) throw cause;
    const error = new ApiError(NETWORK_ERROR, "连不上本地服务", 0, { detail: String(cause) });
    handlers.onError?.(error);
    throw error;
  }

  if (!response.ok) {
    const error = await toApiError(response);
    notifyReloadRequired(error);
    handlers.onError?.(error);
    throw error;
  }
  if (!response.body) {
    const error = new ApiError(SSE_PARSE_ERROR, "服务端没有返回事件流", response.status);
    handlers.onError?.(error);
    throw error;
  }

  const handlersByName = new Map<string, (data: unknown) => void>();
  if (handlers.onIntent) handlersByName.set("intent", handlers.onIntent);
  if (handlers.onDelta) handlersByName.set("delta", handlers.onDelta);
  if (handlers.onDone) handlersByName.set("done", handlers.onDone);
  if (handlers.onError) handlersByName.set("error", (data) => {
    const envelope = (data ?? {}) as Partial<ErrorEnvelope>;
    handlers.onError?.(
      new ApiError(
        envelope.code ?? "INTERNAL",
        envelope.message ?? "流式响应出错",
        response.status,
        { detail: envelope.detail, hint: envelope.hint ?? undefined },
      ),
    );
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let finished = false;

  try {
    while (!finished) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0 && !finished) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        finished = dispatchFrame(frame, handlers, handlersByName) === "stop";
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (!finished && buffer.trim()) dispatchFrame(buffer, handlers, handlersByName);
  } catch (cause) {
    if (isAbortError(cause)) throw cause;
    const error = new ApiError(NETWORK_ERROR, "事件流中断", response.status, {
      detail: String(cause),
    });
    handlers.onError?.(error);
    throw error;
  } finally {
    // 服务端已发 done/error 时主动断开，避免半开连接占着 uvicorn 的 worker
    await reader.cancel().catch(() => undefined);
  }
}
