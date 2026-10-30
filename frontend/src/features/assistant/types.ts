/**
 * 助手页的**界面状态类型**（Owner：助手模块 M4）。
 *
 * 与 `types/api.ts` 的分工：那边是契约（服务端返回什么），这里只描述「一条气泡在页面上
 * 处于什么阶段」。所以本文件允许有 `streaming` / `error` / `createdAt` 这类纯前端字段，
 * 也**不复制**任何服务端字段定义（`intent` / `kind` / `result` / `usage` 全部 import type）。
 */
import type { ApiError } from "@/lib/http";
import type {
  DoneKind,
  FinishReason,
  PlanDraft,
  SearchResponse,
  SseIntentData,
  TokenUsage,
} from "@/types/api";

export type AssistantRole = "user" | "assistant";

/**
 * 一条消息。
 *
 * - `user`：只有 `text` 有意义；
 * - `assistant`：`text` 是 `delta` 的累积；`intent` 来自流首的 `intent` 帧；
 *   `kind` / `result` / `usage` 来自 `done` 帧；`error` 非空时其他都必须忽略。
 */
export interface AssistantMessage {
  /** 前端本地 id（`u-1` / `a-2`），不进任何请求。 */
  id: string;
  role: AssistantRole;
  text: string;
  intent: SseIntentData | null;
  kind: DoneKind | null;
  result: SearchResponse | PlanDraft | null;
  usage: TokenUsage | null;
  error: ApiError | null;
  /** 正在收 `delta`：控制光标与「中止」按钮。 */
  streaming: boolean;
  finishReason: FinishReason | null;
  /** 用户点了「中止」（`done.finish_reason=aborted` 或本地 AbortError）。 */
  aborted: boolean;
  createdAt: number;
}

/** 一条消息带的 token 数（`usage` 缺席按 0，估算值也算进去——§7.10 只要求"估算即可"）。 */
export function messageTokens(message: AssistantMessage): number {
  if (!message.usage) return 0;
  return Math.max(0, message.usage.tokens_in) + Math.max(0, message.usage.tokens_out);
}

/** 本次会话累计 token（含估算；只在内存里，刷新即归零）。 */
export function sessionTokens(messages: readonly AssistantMessage[]): number {
  return messages.reduce((sum, message) => sum + messageTokens(message), 0);
}
