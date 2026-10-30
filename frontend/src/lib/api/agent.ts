/**
 * 助手域 API client（Owner：助手模块 M4，docs/contracts/MODULES.md §3.2）。
 *
 * 只做「路径 + 方法 + body 的搬运」，不缓存、不弹 Toast：
 * - `extractSlots` → `POST /api/agent/extract`（一句话 → 表单字段，API.md §3.16）；
 * - `planDraft`   → `POST /api/agent/plan-draft`（表单 → 草稿，**不落库**，§3.17）；
 * - `chatStream`  → `POST /api/agent/chat`（SSE，§3.18 / §4）。
 *
 * SSE 解析**不在本文件**：唯一实现在 `lib/http.ts::readSSE`（D18）。这里只做两件事：
 * ① 把 `unknown` 载荷收敛成 `types/api.ts` 的契约类型（帧结构不该让页面去断言）；
 * ② 保持 MODULES §3.2 的函数签名，`handlers` 只暴露四个事件回调。
 */
import { readSSE, request, type ApiError } from "@/lib/http";
import type {
  AgentChatRequest,
  AgentExtractRequest,
  AgentExtractResponse,
  AgentPlanDraftRequest,
  Intent,
  PlanDraft,
  RouteSource,
  SseDeltaData,
  SseDoneData,
  SseIntentData,
} from "@/types/api";

/** `POST /api/agent/extract`（429 限流 10/min；未配模型 400 `LLM_NOT_CONFIGURED`）。 */
export function extractSlots(body: AgentExtractRequest): Promise<AgentExtractResponse> {
  return request<AgentExtractResponse>("POST", "/api/agent/extract", { body });
}

/** `POST /api/agent/plan-draft`（响应体**就是** `PlanDraft`，没有外层包装）。 */
export function planDraft(body: AgentPlanDraftRequest): Promise<PlanDraft> {
  return request<PlanDraft>("POST", "/api/agent/plan-draft", { body });
}

/** `chatStream` 的事件回调（MODULES §3.2 的签名，字段一个不多一个不少）。 */
export interface ChatStreamHandlers {
  onIntent?: (data: SseIntentData) => void;
  onDelta?: (data: SseDeltaData) => void;
  onDone?: (data: SseDoneData) => void;
  onError?: (error: ApiError) => void;
}

/* ------------------------------------------------------ 帧载荷的类型收敛 */

const INTENTS: readonly Intent[] = [
  "GREETING",
  "CAPABILITY",
  "RESOURCE_SEARCH",
  "PLAN_CREATE",
  "PLAN_ADJUST",
  "ADVICE",
  "CLARIFY",
  "OUT_OF_SCOPE",
];

const SOURCES: readonly RouteSource[] = ["rule", "llm", "fallback"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asIntent(value: unknown): SseIntentData | null {
  if (!isRecord(value)) return null;
  const intent = value.intent;
  if (typeof intent !== "string" || !(INTENTS as readonly string[]).includes(intent)) {
    return null;
  }
  const source = value.source;
  const clarify = value.clarify_options;
  return {
    intent: intent as Intent,
    confidence: typeof value.confidence === "number" ? value.confidence : 0,
    source:
      typeof source === "string" && (SOURCES as readonly string[]).includes(source)
        ? (source as RouteSource)
        : "fallback",
    slots: isRecord(value.slots) ? (value.slots as SseIntentData["slots"]) : null,
    needs_clarify: value.needs_clarify === true,
    clarify_options: Array.isArray(clarify)
      ? clarify.filter((item): item is string => typeof item === "string").slice(0, 3)
      : [],
  };
}

function asDelta(value: unknown): SseDeltaData | null {
  if (!isRecord(value) || typeof value.text !== "string") return null;
  return { text: value.text };
}

/**
 * `done` 的 `result` 由 `kind` 决定（§4.3）：`search` → `SearchResponse`，
 * `plan_draft` → `PlanDraft`，其余 `null`。这里不深校验 result —— 页面按 `kind` 分支后再收窄。
 */
function asDone(value: unknown): SseDoneData | null {
  if (!isRecord(value)) return null;
  const kind = value.kind;
  if (kind !== "text" && kind !== "search" && kind !== "plan_draft" && kind !== "clarify") {
    return null;
  }
  const usage = isRecord(value.usage) ? value.usage : {};
  const finish = value.finish_reason;
  const doneIntent = value.intent;
  return {
    kind,
    message_id: typeof value.message_id === "number" ? value.message_id : null,
    finish_reason:
      finish === "length" || finish === "aborted" || finish === "stop" ? finish : "stop",
    aborted: value.aborted === true,
    usage: {
      tokens_in: typeof usage.tokens_in === "number" ? usage.tokens_in : 0,
      tokens_out: typeof usage.tokens_out === "number" ? usage.tokens_out : 0,
      estimated: usage.estimated !== false,
    },
    intent:
      typeof doneIntent === "string" && (INTENTS as readonly string[]).includes(doneIntent)
        ? (doneIntent as Intent)
        : null,
    result: isRecord(value.result) ? (value.result as unknown as SseDoneData["result"]) : null,
  };
}

/**
 * `POST /api/agent/chat` 的 SSE 版本（§3.18 / §4）。
 *
 * - 流前失败（401/429/未配模型/参数）由 `readSSE` 抛 `ApiError`，**同时**会先调 `onError`；
 * - 流内 `event: error` 也走 `onError`，之后流立刻结束；
 * - `signal.abort()` 会真的断开连接（用户点「中止」/ 切路由卸载），此时抛 `AbortError`，
 *   调用方按 `signal.aborted` 判断，不当成错误展示。
 */
export function chatStream(
  body: AgentChatRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return readSSE(
    "/api/agent/chat",
    body,
    {
      onIntent: (data) => {
        const intent = asIntent(data);
        if (intent) handlers.onIntent?.(intent);
      },
      onDelta: (data) => {
        const delta = asDelta(data);
        if (delta) handlers.onDelta?.(delta);
      },
      onDone: (data) => {
        const done = asDone(data);
        if (done) handlers.onDone?.(done);
      },
      onError: (error) => handlers.onError?.(error),
    },
    signal,
  );
}
