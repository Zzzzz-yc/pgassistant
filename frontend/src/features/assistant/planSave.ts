/**
 * 计划自动落库的状态机（Owner：助手模块 M4，C4）。
 *
 * 为什么单独一个文件：`useAgentStream` 里直接写会被 React/Query 包住，没法单测；
 * 这里只负责三件事，注入的 `saveDraft` 才是真正的 `POST /api/plans`：
 *
 * 1. **只保存一次**：按消息 id 去重——已成功的不再发请求，正在飞的回同一个 Promise
 *    （重复 `done`、双击「重试」、组件重挂载都不会建出两份计划）；
 * 2. **失败保留草稿**：失败只标记 `failed`，草稿仍在内存里，气泡的「保存失败，重试」
 *    再调一次 `save()` 即可；
 * 3. **状态写回消息**：通过 `patchThreadMessage` 把 `saving / saved / failed` 与
 *    `plan_id` 写进模块级消息 store（切页面回来仍能看到结果）。
 */
import type { PlanDraft, PlanRead } from "@/types/api";

import { patchThreadMessage } from "./thread";
import type { PlanSaveState } from "./types";

export interface PlanSaveResult {
  state: PlanSaveState;
  planId: number | null;
}

export interface PlanSaver {
  /** 注入真正的保存实现（`useSaveDraft().mutateAsync`）；挂载时调用。 */
  setSaveImpl: (impl: (draft: PlanDraft) => Promise<PlanRead>) => void;
  /** 记住这条消息的完整草稿（只进内存，不进消息对象）。 */
  remember: (assistantId: string, draft: PlanDraft) => void;
  /** 消息重新生成时作废旧草稿与"已保存"标记。 */
  forget: (assistantId: string) => void;
  hasDraft: (assistantId: string) => boolean;
  /** 返回 null 表示没有草稿/没有实现，什么都没做。 */
  save: (assistantId: string) => Promise<PlanSaveResult | null>;
}

export function createPlanSaver(
  initialSave?: (draft: PlanDraft) => Promise<PlanRead>,
): PlanSaver {
  const drafts = new Map<string, PlanDraft>();
  /** 已成功落库：message id → plan id。 */
  const saved = new Map<string, number>();
  /** 正在保存：message id → 同一个 Promise（并发去重）。 */
  const inflight = new Map<string, Promise<PlanSaveResult>>();
  let impl = initialSave ?? null;

  const notify = (assistantId: string, state: PlanSaveState, planId: number | null): void => {
    patchThreadMessage(assistantId, (message) =>
      message.plan
        ? {
            ...message,
            plan: {
              ...message.plan,
              saveState: state,
              planId: planId ?? message.plan.planId,
            },
          }
        : message,
    );
  };

  return {
    setSaveImpl(next) {
      impl = next;
    },
    remember(assistantId, draft) {
      drafts.set(assistantId, draft);
    },
    forget(assistantId) {
      drafts.delete(assistantId);
      saved.delete(assistantId);
    },
    hasDraft(assistantId) {
      return drafts.has(assistantId);
    },
    save(assistantId) {
      const done = saved.get(assistantId);
      if (done !== undefined) {
        return Promise.resolve({ state: "saved" as const, planId: done });
      }
      const running = inflight.get(assistantId);
      if (running) return running;

      const draft = drafts.get(assistantId);
      const save = impl;
      if (!draft || !save) return Promise.resolve(null);

      notify(assistantId, "saving", null);
      const task = (async (): Promise<PlanSaveResult> => {
        try {
          const plan = await save(draft);
          saved.set(assistantId, plan.id);
          drafts.delete(assistantId);
          notify(assistantId, "saved", plan.id);
          return { state: "saved", planId: plan.id };
        } catch {
          // 草稿不删：气泡里的「保存失败，重试」还要用它
          notify(assistantId, "failed", null);
          return { state: "failed", planId: null };
        } finally {
          inflight.delete(assistantId);
        }
      })();
      inflight.set(assistantId, task);
      return task;
    },
  };
}

/** 进程内单例：切路由 / 重新挂载后仍按消息 id 去重（C4）。 */
export const planSaver = createPlanSaver();
