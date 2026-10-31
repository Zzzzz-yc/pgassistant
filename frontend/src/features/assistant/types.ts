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

/** 计划落库的四个阶段（C4：`done.kind=plan_draft` 后自动保存）。 */
export type PlanSaveState = "pending" | "saving" | "saved" | "failed";

/**
 * 计划结果的**摘要**。
 *
 * 完整 `PlanDraft` 只留在 `hooks.ts` 的模块级草稿缓存里（保存失败时重试用），**不进消息
 * 对象**：消息会进会话 store，带上整棵阶段/条目会让内存与后续持久化都膨胀（C4）。
 */
export interface PlanOutcome {
  /** 保存成功后才有；失败/进行中为 null。 */
  planId: number | null;
  title: string;
  weeks: number;
  weeklyHours: number;
  stages: number;
  items: number;
  /** 总估时（分钟），气泡里换算成「约 N 小时」。 */
  minutes: number;
  saveState: PlanSaveState;
}

/** 从草稿生成摘要；不保留草稿内容。 */
export function planOutcomeFromDraft(draft: PlanDraft): PlanOutcome {
  const minutes = draft.stages.reduce(
    (sum, stage) =>
      sum + stage.items.reduce((inner, item) => inner + (item.est_minutes ?? 30), 0),
    0,
  );
  return {
    planId: null,
    title: draft.title,
    weeks: draft.weeks,
    weeklyHours: draft.weekly_hours,
    stages: draft.stages.length,
    items: draft.stages.reduce((sum, stage) => sum + stage.items.length, 0),
    minutes,
    saveState: "pending",
  };
}

/**
 * 一条消息。
 *
 * - `user`：只有 `text` 有意义；
 * - `assistant`：`text` 是 `delta` 的累积；`intent` 来自流首的 `intent` 帧；
 *   `kind` / `usage` 来自 `done` 帧；`result` 只装检索结果，计划只留 `plan` 摘要；
 *   `error` 非空时其他都必须忽略。
 */
export interface AssistantMessage {
  /** 前端本地 id（`u-1` / `a-2`），不进任何请求。 */
  id: string;
  role: AssistantRole;
  text: string;
  intent: SseIntentData | null;
  kind: DoneKind | null;
  result: SearchResponse | null;
  /** 计划摘要（C4）；完整 draft 不进消息对象。 */
  plan: PlanOutcome | null;
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
