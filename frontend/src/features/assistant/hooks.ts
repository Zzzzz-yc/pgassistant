/**
 * 助手模块数据层（Owner：助手模块 M4，docs/contracts/MODULES.md §3.3）。
 *
 * 导出两件事：
 * 1. `useAgentStream()` —— 契约要求的 `{send, abort, streaming, messages}`。**额外多一个
 *    `retry(assistantId)`**：重试必须复用原来的用户气泡（不能把同一句话再追加一遍），
 *    所以重试入口只能在 hook 内部（它知道"这条助手消息前面那条 user 消息是什么"）。
 *    其余四个键的形状与契约逐字一致，页面之外没有消费者。
 * 2. `useSaveDraft()` —— 草稿 → `POST /api/plans`（§3.9 的 `PlanCreate`）。
 *
 * 三条硬约束：
 * - **卸载即中止**：`useEffect` 的清理函数 `abort()`，避免切路由后上游还在烧 token（§7.9）；
 * - **`done` 后收敛**：`readSSE` 收到 `done` 就结束读流，这里把 `streaming` 置回 false；
 * - **`error` 优先**：`error` 帧之后不再有 `done`（§4.2 第 2 条），所以 `error` 一旦写入，
 *   该消息的 `text` 保持原样但不再被认为在流式（页面按 `error` 分支渲染错误卡片）。
 */
import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { planKeys } from "@/features/plans/hooks";
import { currentLocale } from "@/i18n";
import { chatStream } from "@/lib/api/agent";
import { createPlan } from "@/lib/api/plans";
import { ApiError } from "@/lib/http";
import type { PlanDraft, PlanRead, SearchResponse } from "@/types/api";

import { type AssistantMessage } from "./types";

/* ------------------------------------------------------------- 结果收窄 */

/** `done.result` 在 `kind=search` 时是 `SearchResponse`（§4.3）。 */
export function isSearchResult(result: unknown): result is SearchResponse {
  return (
    typeof result === "object" &&
    result !== null &&
    Array.isArray((result as SearchResponse).items) &&
    typeof (result as SearchResponse).total === "number"
  );
}

/** `done.result` 在 `kind=plan_draft` 时是 `PlanDraft`（§4.3）。 */
export function isPlanDraftResult(result: unknown): result is PlanDraft {
  return (
    typeof result === "object" &&
    result !== null &&
    typeof (result as PlanDraft).title === "string" &&
    Array.isArray((result as PlanDraft).stages)
  );
}

/* ---------------------------------------------------------------- 工具 */

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** 兜底成 `ApiError`：`readSSE` 抛的非 `ApiError` 只可能是程序错误，也要显示成人话。 */
function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError("NETWORK_ERROR", "连不上本地服务", 0, { detail: String(error) });
}

function emptyAssistant(id: string, createdAt: number): AssistantMessage {
  return {
    id,
    role: "assistant",
    text: "",
    intent: null,
    kind: null,
    result: null,
    usage: null,
    error: null,
    streaming: true,
    finishReason: null,
    aborted: false,
    createdAt,
  };
}

function userMessage(id: string, text: string, createdAt: number): AssistantMessage {
  return {
    id,
    role: "user",
    text,
    intent: null,
    kind: null,
    result: null,
    usage: null,
    error: null,
    streaming: false,
    finishReason: "stop",
    aborted: false,
    createdAt,
  };
}

/* ------------------------------------------------------------- 主 hook */

export interface AgentStreamApi {
  /** 发送一句话（会追加「用户气泡 + 助手占位气泡」）。空串/正在流式中直接忽略。 */
  send: (text: string) => void;
  /** 重发某条助手消息（不追加用户气泡）；`assistantId` 是失败/超时那条的 id。 */
  retry: (assistantId: string) => void;
  /** 中止当前流（用户点「中止」/ 组件卸载）。 */
  abort: () => void;
  streaming: boolean;
  messages: AssistantMessage[];
}

/**
 * 对话流：一次只允许一条在飞（`§7.10 全局并发 ≤2` 是后端的事，前端单线程对话足够）。
 * 会话不持久化（`§7.8`：历史在 `chat_messages`，刷新页面即开新线程）。
 */
export function useAgentStream(): AgentStreamApi {
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [streaming, setStreaming] = useState(false);

  const active = useRef<{ controller: AbortController; assistantId: string } | null>(null);
  const mounted = useRef(true);
  const seq = useRef(0);
  const messagesRef = useRef<AssistantMessage[]>([]);

  // `retry` 需要读当前消息（找上一条 user 文本），用 ref 拿最新值而不是让 send 依赖 messages
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      active.current?.controller.abort();
      active.current = null;
    };
  }, []);

  const patch = useCallback(
    (assistantId: string, update: (message: AssistantMessage) => AssistantMessage) => {
      if (!mounted.current) return;
      setMessages((prev) =>
        prev.map((message) => (message.id === assistantId ? update(message) : message)),
      );
    },
    [],
  );

  const run = useCallback(
    async (prompt: string, assistantId: string) => {
      const controller = new AbortController();
      active.current = { controller, assistantId };
      if (mounted.current) setStreaming(true);

      let errorSeen = false;
      try {
        await chatStream(
          { message: prompt, locale: currentLocale(), page_context: null },
          {
            onIntent: (data) => patch(assistantId, (message) => ({ ...message, intent: data })),
            onDelta: (data) =>
              patch(assistantId, (message) => ({ ...message, text: message.text + data.text })),
            onDone: (data) =>
              patch(assistantId, (message) => ({
                ...message,
                streaming: false,
                kind: data.kind,
                result: data.result,
                usage: data.usage,
                finishReason: data.finish_reason,
                aborted: data.aborted,
              })),
            onError: (error) => {
              errorSeen = true;
              patch(assistantId, (message) => ({ ...message, streaming: false, error }));
            },
          },
          controller.signal,
        );
      } catch (error) {
        if (isAbortError(error)) {
          // 用户中止 / 卸载：不是错误，按「已中止」收尾（§7.9 客户端断连）
          patch(assistantId, (message) => ({
            ...message,
            streaming: false,
            aborted: true,
            finishReason: "aborted",
          }));
        } else if (!errorSeen) {
          const apiError = toApiError(error);
          patch(assistantId, (message) => ({ ...message, streaming: false, error: apiError }));
        }
      } finally {
        if (active.current?.controller === controller) active.current = null;
        if (mounted.current) setStreaming(false);
      }
    },
    [patch],
  );

  const send = useCallback(
    (raw: string) => {
      const prompt = raw.trim();
      if (!prompt || active.current) return;
      seq.current += 1;
      const userId = `u-${seq.current}`;
      seq.current += 1;
      const assistantId = `a-${seq.current}`;
      const createdAt = Date.now();
      setMessages((prev) => [
        ...prev,
        userMessage(userId, prompt, createdAt),
        emptyAssistant(assistantId, createdAt),
      ]);
      void run(prompt, assistantId);
    },
    [run],
  );

  const retry = useCallback(
    (assistantId: string) => {
      if (active.current) return;
      const list = messagesRef.current;
      const index = list.findIndex((message) => message.id === assistantId);
      if (index < 0) return;

      let prompt = "";
      for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
        if (list[cursor].role === "user") {
          prompt = list[cursor].text;
          break;
        }
      }
      if (!prompt) return;

      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                ...emptyAssistant(assistantId, message.createdAt),
              }
            : message,
        ),
      );
      void run(prompt, assistantId);
    },
    [run],
  );

  const abort = useCallback(() => {
    active.current?.controller.abort();
  }, []);

  return { send, retry, abort, streaming, messages };
}

/* ------------------------------------------------------------ 保存草稿 */

/**
 * 草稿 → 计划（`POST /api/plans`，§3.9）。
 *
 * `origin: "agent"`（草稿来自对话）、`set_active: true`（用户点了「保存为计划」就是要用它，
 * 与 `/plans/new` 的保存行为一致）、`start_level` 草稿里没有，按 `beginner` 落库。
 * 成功后按 MODULES §3.3 的缓存规则写 `["plan", id]` 并让 `["plans"]` 失效。
 */
export function useSaveDraft(): UseMutationResult<PlanRead, unknown, PlanDraft> {
  const queryClient = useQueryClient();

  return useMutation<PlanRead, unknown, PlanDraft>({
    mutationFn: (draft) =>
      createPlan({
        draft,
        origin: "agent",
        template_id: null,
        start_level: "beginner",
        set_active: true,
      }),
    onSuccess: (plan) => {
      queryClient.setQueryData(planKeys.detail(plan.id), plan);
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
    },
  });
}
