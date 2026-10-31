/**
 * 助手模块数据层（Owner：助手模块 M4，docs/contracts/MODULES.md §3.3）。
 *
 * 导出两件事：
 * 1. `useAgentStream()` —— 契约要求的 `{send, abort, streaming, messages}`。**额外多两个
 *    方法**：`retry(assistantId)`（重试失败的回答，复用原来的用户气泡）与
 *    `retryPlanSave(assistantId)`（C4：计划自动保存失败后重试）。
 *    其余四个键的形状与契约逐字一致，页面之外没有消费者。
 * 2. `useSaveDraft()` —— 草稿 → `POST /api/plans`（§3.9 的 `PlanCreate`）。
 *
 * 五条硬约束：
 * - **会话内不丢**：消息存在模块级 store（`features/assistant/thread.ts`），切路由回来
 *   还在；刷新 / 关标签页即清空。**令牌不进 sessionStorage，也不进这个 store**（§13.7）；
 * - **卸载即中止**：`useEffect` 的清理函数 `abort()`，避免切路由后上游还在烧 token（§7.9），
 *   同时把在飞的消息标成「已中止」，回来的页面不会留下永不结束的「正在输入」气泡；
 * - **`done` 后收敛**：`readSSE` 收到 `done` 就结束读流，这里把 `streaming` 置回 false；
 * - **`error` 优先**：`error` 帧之后不再有 `done`（§4.2 第 2 条），所以 `error` 一旦写入，
 *   该消息的 `text` 保持原样但不再被认为在流式（页面按 `error` 分支渲染错误卡片）；
 * - **计划自动落库（C4）**：`done.kind=plan_draft` 后立刻 `POST /api/plans`，消息里只留
 *   `PlanOutcome` 摘要；完整草稿由 `planSave.ts` 的进程内单例持有，失败时供「重试」使用。
 *   同一消息 id 的成功保存只做一次（按 id 去重），防重渲染/重复 done 建两份计划。
 */
import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";

import { planKeys } from "@/features/plans/hooks";
import { currentLocale } from "@/i18n";
import { chatStream } from "@/lib/api/agent";
import { createPlan } from "@/lib/api/plans";
import { ApiError } from "@/lib/http";
import type { PlanDraft, PlanRead, SearchResponse } from "@/types/api";

import {
  getThreadSnapshot,
  nextMessageId,
  patchThreadMessage,
  setThreadMessages,
  setThreadStreaming,
  subscribeThread,
} from "./thread";
import { planSaver } from "./planSave";
import { planOutcomeFromDraft, type AssistantMessage } from "./types";

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

/* ------------------------------------------------- 计划草稿缓存（内存） */

// 完整草稿、去重集合与保存状态机都在 `planSave.ts` 的进程内单例里：
// 草稿**不进消息对象**；消息只留 `PlanOutcome` 摘要（C4）。

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
    plan: null,
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
    plan: null,
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
  /** 重试一条计划的自动保存（C4：保存失败时气泡里的按钮）。 */
  retryPlanSave: (assistantId: string) => void;
  /** 中止当前流（用户点「中止」/ 组件卸载）。 */
  abort: () => void;
  streaming: boolean;
  messages: AssistantMessage[];
}

/**
 * 对话流：一次只允许一条在飞（`§7.10 全局并发 ≤2` 是后端的事，前端单线程对话足够）。
 *
 * 消息状态**不放在组件里**：`useSyncExternalStore` 订阅模块级 store，所以 `/assistant`
 * 卸载再挂载（切到资料页再回来）对话仍在；关闭标签页 / 刷新后模块重新求值即清空。
 */
export function useAgentStream(): AgentStreamApi {
  const snapshot = useSyncExternalStore(
    subscribeThread,
    getThreadSnapshot,
    getThreadSnapshot,
  );
  const { messages, streaming } = snapshot;

  const saveMutation = useSaveDraft();
  const active = useRef<{ controller: AbortController; assistantId: string } | null>(null);
  const messagesRef = useRef<AssistantMessage[]>(messages);

  // `POST /api/plans` 的实现注入给进程内单例（成功后的 query 失效仍由 useSaveDraft 做）
  useEffect(() => {
    planSaver.setSaveImpl((draft) => saveMutation.mutateAsync(draft));
  }, [saveMutation]);

  // `retry` 需要读当前消息（找上一条 user 文本），用 ref 拿最新值而不是让 send 依赖 messages
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    return () => {
      const running = active.current;
      active.current = null;
      running?.controller.abort();
      if (!running) return;
      // 同步收尾：不依赖异步 catch —— 组件已经卸载，但 store 还在，回来时状态必须自洽
      patchThreadMessage(running.assistantId, (message) =>
        message.streaming
          ? { ...message, streaming: false, aborted: true, finishReason: "aborted" }
          : message,
      );
      setThreadStreaming(false);
    };
  }, []);

  const patch = useCallback(
    (assistantId: string, update: (message: AssistantMessage) => AssistantMessage) => {
      patchThreadMessage(assistantId, update);
    },
    [],
  );

  const run = useCallback(
    async (prompt: string, assistantId: string) => {
      const controller = new AbortController();
      active.current = { controller, assistantId };
      setThreadStreaming(true);

      let errorSeen = false;
      try {
        await chatStream(
          { message: prompt, locale: currentLocale(), page_context: null },
          {
            onIntent: (data) => patch(assistantId, (message) => ({ ...message, intent: data })),
            onDelta: (data) =>
              patch(assistantId, (message) => ({ ...message, text: message.text + data.text })),
            onDone: (data) => {
              // C4：计划只留摘要，完整草稿进内存缓存，并立刻自动保存到计划页。
              const draft =
                data.kind === "plan_draft" && isPlanDraftResult(data.result)
                  ? data.result
                  : null;
              patch(assistantId, (message) => ({
                ...message,
                streaming: false,
                kind: data.kind,
                result:
                  data.kind === "search" && isSearchResult(data.result) ? data.result : null,
                plan: draft ? planOutcomeFromDraft(draft) : null,
                usage: data.usage,
                finishReason: data.finish_reason,
                aborted: data.aborted,
              }));
              if (draft) {
                planSaver.remember(assistantId, draft);
                void planSaver.save(assistantId);
              }
            },
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
        setThreadStreaming(false);
      }
    },
    [patch],
  );

  const send = useCallback(
    (raw: string) => {
      const prompt = raw.trim();
      if (!prompt || active.current) return;
      const userId = nextMessageId("u");
      const assistantId = nextMessageId("a");
      const createdAt = Date.now();
      setThreadMessages((prev) => [
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

      // 这条消息要重新生成：旧草稿与"已保存"标记一并作废（新结果可以再存一次）
      planSaver.forget(assistantId);
      setThreadMessages((prev) =>
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

  const retryPlanSave = useCallback((assistantId: string) => {
    void planSaver.save(assistantId);
  }, []);

  const abort = useCallback(() => {
    active.current?.controller.abort();
  }, []);

  return { send, retry, retryPlanSave, abort, streaming, messages };
}

/* ------------------------------------------------------------ 保存草稿 */

/**
 * 草稿 → 计划（`POST /api/plans`，§3.9）。
 *
 * `origin: "agent"`（草稿来自对话）、`set_active: true`（自动落库的语义就是"用它"，
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
