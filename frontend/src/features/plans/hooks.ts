/**
 * 计划模块数据层（Owner：计划模块 M3，docs/contracts/MODULES.md §3.3）。
 *
 * 三条硬约定，验收会逐条查：
 * 1. **query key**：列表 `["plans", params]`、详情 `["plan", id]`、模板 `["plan-templates"]`。
 * 2. **写操作成功后**：服务端已返回整棵 `PlanRead` → 直接 `setQueryData(["plan", id], result.plan)`，
 *    再 `invalidateQueries(["plans"])`；不补 `GET`（D6）。
 * 3. **409 `VERSION_CONFLICT`**：用 `detail.current` 覆盖缓存（不重试写操作，D4 第 3 条）
 *    → Toast「已在别处更新，已为你刷新」→ 用户手动重试。
 *
 * 两条实测踩过的坑，改之前先读：
 * - **乐观更新不得改 `version`**：React Query 先跑 `onMutate` 再跑 `mutationFn`，
 *   若 `onMutate` 里把 version +1，`mutationFn` 读到的就是"未来的版本"，服务端必然回 409。
 *   所以乐观态只改内容，version 一律取缓存里的服务端真值。
 * - **条目级 409 的 `detail.current` 是 `ItemRead`**（MODULES §2.3），不是整棵计划；
 *   直接塞进 `["plan", id]` 会把缓存写成没有 `stages` 的对象，页面立刻 TypeError。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { errorMessage } from "@/i18n";
import {
  addPlanItem,
  addStage,
  createPlan,
  deletePlan,
  deletePlanItem,
  deleteStage,
  getPlan,
  getPlanTemplate,
  listPlanTemplates,
  listPlans,
  patchPlan,
  patchPlanItem,
  patchStage,
} from "@/lib/api/plans";
import { ApiError } from "@/lib/http";
import { pushToast, useToast } from "@/lib/query";
import type {
  ItemPatch,
  ItemRead,
  PlanCreate,
  PlanDraft,
  PlanItemStatus,
  PlanListQuery,
  PlanListResponse,
  PlanMutationResult,
  PlanPatch,
  PlanProgress,
  PlanRead,
  PlanStatus,
  PlanTemplateListResponse,
  StageRead,
} from "@/types/api";

/* ------------------------------------------------------------------ keys */

export const planKeys = {
  all: ["plans"] as const,
  list: (params: PlanListQuery = {}) => ["plans", params] as const,
  detail: (id: number) => ["plan", id] as const,
  templates: ["plan-templates"] as const,
  template: (id: string) => ["plan-template", id] as const,
};

/* ------------------------------------------------- 进度（乐观更新用） */

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/** §8.4 / §4.6：阶段按条数算，计划总进度**按时长加权**，skipped 不进分母。 */
export function computeProgress(plan: PlanRead): PlanProgress {
  const stages = plan.stages.map((stage) => {
    const done = stage.items.filter((item) => item.status === "done").length;
    const todo = stage.items.filter((item) => item.status === "todo").length;
    const skipped = stage.items.filter((item) => item.status === "skipped").length;
    const denominator = done + todo;
    return {
      stage_id: stage.id,
      done,
      todo,
      skipped,
      percent: denominator === 0 ? 0 : round1((done / denominator) * 100),
      est_minutes: stage.items.reduce((sum, item) => sum + item.est_minutes, 0),
    };
  });

  const items = plan.stages.flatMap((stage) => stage.items);
  const minutesOf = (status: PlanItemStatus) =>
    items
      .filter((item) => item.status === status)
      .reduce((sum, item) => sum + item.est_minutes, 0);

  const doneMinutes = minutesOf("done");
  const todoMinutes = minutesOf("todo");
  const weighted = doneMinutes + todoMinutes;
  const percent = weighted === 0 ? 0 : round1((doneMinutes / weighted) * 100);

  return {
    percent,
    done_items: items.filter((item) => item.status === "done").length,
    todo_items: items.filter((item) => item.status === "todo").length,
    skipped_items: items.filter((item) => item.status === "skipped").length,
    total_minutes: items.reduce((sum, item) => sum + item.est_minutes, 0),
    invested_minutes: doneMinutes,
    remaining_weeks: round1(plan.horizon_weeks * (1 - percent / 100)),
    stages,
  };
}

/** 改完结构后重算 `progress` 与每个 `stage.progress`，让乐观态也自洽。 */
export function withRecomputedProgress(plan: PlanRead): PlanRead {
  const progress = computeProgress(plan);
  const stageById = new Map(progress.stages.map((entry) => [entry.stage_id, entry]));
  return {
    ...plan,
    stages: plan.stages.map((stage) => ({
      ...stage,
      progress: stageById.get(stage.id) ?? stage.progress,
    })),
    progress,
  };
}

function sortTree(plan: PlanRead): PlanRead {
  const byOrder = <T extends { order_index: number }>(left: T, right: T) =>
    left.order_index - right.order_index;
  return {
    ...plan,
    stages: [...plan.stages]
      .sort(byOrder)
      .map((stage) => ({ ...stage, items: [...stage.items].sort(byOrder) })),
  };
}

/* --------------------------------------------------------------- 只读查询 */

export function usePlans(params: PlanListQuery = {}): UseQueryResult<PlanListResponse> {
  return useQuery({
    queryKey: planKeys.list(params),
    queryFn: () => listPlans(params),
  });
}

export function usePlan(id: number | null | undefined): UseQueryResult<PlanRead> {
  return useQuery({
    queryKey: planKeys.detail(id ?? -1),
    queryFn: () => getPlan(id as number),
    enabled: typeof id === "number" && Number.isInteger(id) && id > 0,
  });
}

export function usePlanTemplates(): UseQueryResult<PlanTemplateListResponse> {
  return useQuery({
    queryKey: planKeys.templates,
    queryFn: listPlanTemplates,
    // 模板来自仓库里的 YAML，进程内不会变；避免切页回来重复请求
    staleTime: 5 * 60_000,
  });
}

/** 选中模板后才取明细（`GET /api/plan-templates/{id}` → `PlanDraft`）。 */
export function usePlanTemplate(
  templateId: string | null | undefined,
): UseQueryResult<PlanDraft> {
  return useQuery({
    queryKey: planKeys.template(templateId ?? ""),
    queryFn: () => getPlanTemplate(templateId as string),
    enabled: !!templateId,
    staleTime: 5 * 60_000,
  });
}

/** 当前唯一 active 计划的 id（§8.1：同一时间仅一个）。 */
export function useActivePlanId(): {
  planId: number | null;
  isLoading: boolean;
  hasAny: boolean;
} {
  const { data, isLoading } = usePlans({ status: "active" });
  const activeId =
    data?.active_plan_id ?? data?.items.find((item) => item.status === "active")?.id ?? null;
  return {
    planId: activeId,
    isLoading,
    hasAny: (data?.total ?? 0) > 0 || activeId !== null,
  };
}

/** 侧栏计划树用：有 active 计划则取整棵（含阶段与条目）。 */
export function useActivePlan(): { plan: PlanRead | null; isLoading: boolean } {
  const { planId, isLoading: listLoading } = useActivePlanId();
  const detail = usePlan(planId ?? undefined);
  return {
    plan: detail.data ?? null,
    isLoading: listLoading || (planId !== null && detail.isLoading),
  };
}

/* ------------------------------------------------------ 错误 / 冲突处理 */

interface ConflictDetail {
  expected_version?: number;
  actual_version?: number;
  /** 计划级冲突给 `PlanRead`，**条目级冲突给 `ItemRead`**（MODULES §2.3）。 */
  current?: unknown;
}

/** 只有带 `stages` 数组的才是整棵计划；条目级的 `current` 不能写进 `["plan", id]`。 */
function isPlanRead(value: unknown): value is PlanRead {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as PlanRead).stages)
  );
}

function readConflictCurrent(error: unknown): PlanRead | null {
  if (!(error instanceof ApiError) || error.code !== "VERSION_CONFLICT") return null;
  const detail = error.detail as ConflictDetail | undefined;
  return isPlanRead(detail?.current) ? detail.current : null;
}

export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === "VERSION_CONFLICT";
}

/**
 * 统一的失败处理：409 用 `detail.current` 覆盖缓存并提示「已为你刷新」；
 * 其它错误只弹 Toast（页面自己决定要不要额外展示 ErrorState）。
 *
 * 条目级 409 的 `current` 是单条 `ItemRead`——没有整棵计划可写，改为让详情重新取。
 */
export function handlePlanError(
  queryClient: QueryClient,
  planId: number | null,
  error: unknown,
  notify: (message: string, kind: "warn" | "error") => void,
): void {
  const current = readConflictCurrent(error);
  if (current) {
    queryClient.setQueryData(planKeys.detail(current.id), current);
    void queryClient.invalidateQueries({ queryKey: planKeys.all });
    notify("conflict.refreshed", "warn");
    return;
  }
  if (isVersionConflict(error)) {
    if (planId !== null) {
      void queryClient.invalidateQueries({ queryKey: planKeys.detail(planId) });
    }
    void queryClient.invalidateQueries({ queryKey: planKeys.all });
    notify("conflict.refreshed", "warn");
    return;
  }
  if (error instanceof ApiError) {
    notify(error.code, "error");
    return;
  }
  notify("INTERNAL", "error");
}

/** 组件内版本：把 i18n 文案与 Toast 接进 `handlePlanError`。 */
function usePlanErrorHandler(planId: number | null) {
  const queryClient = useQueryClient();
  const { t } = useTranslation("plans");
  const toastApi = useToast();

  return useCallback(
    (error: unknown) => {
      handlePlanError(queryClient, planId, error, (message, kind) => {
        // `message` 命中 plans 命名空间的固定文案就用它，否则当作错误码翻译（§9.9 集中映射）
        const isKey = message.startsWith("conflict.") || message.startsWith("toast.");
        toastApi.push({
          kind,
          message: isKey ? t(message) : errorMessage(message),
        });
      });
    },
    [queryClient, planId, t, toastApi],
  );
}

/* ------------------------------------------------------------- 写操作 */

interface MutateContext {
  previous?: PlanRead;
}

function currentPlanVersion(queryClient: QueryClient, planId: number): number {
  return queryClient.getQueryData<PlanRead>(planKeys.detail(planId))?.version ?? 1;
}

/** 条目级 version：PATCH/DELETE 条目校验的是**条目自己**的 version（D4）。 */
function itemVersionOf(queryClient: QueryClient, planId: number, itemId: number): number {
  return (
    queryClient
      .getQueryData<PlanRead>(planKeys.detail(planId))
      ?.stages.flatMap((stage) => stage.items)
      .find((item) => item.id === itemId)?.version ?? 1
  );
}

/** 写计划树的公共骨架：乐观改内容 → 成功用返回的整棵树覆盖 → 失败回滚 / 409 刷新。 */
function usePlanTreeMutation<TVariables>(
  planId: number,
  optimistic: (plan: PlanRead, variables: TVariables) => PlanRead,
  run: (variables: TVariables) => Promise<PlanMutationResult>,
) {
  const queryClient = useQueryClient();
  const onError = usePlanErrorHandler(planId);

  return useMutation<PlanMutationResult, unknown, TVariables, MutateContext>({
    mutationFn: run,
    onMutate: async (variables) => {
      await queryClient.cancelQueries({ queryKey: planKeys.detail(planId) });
      const previous = queryClient.getQueryData<PlanRead>(planKeys.detail(planId));
      if (previous) {
        queryClient.setQueryData<PlanRead>(
          planKeys.detail(planId),
          withRecomputedProgress(sortTree(optimistic(previous, variables))),
        );
      }
      return { previous };
    },
    onError: (error, _variables, context) => {
      // 409 不回滚：`handlePlanError` 会写入服务端的最新实体（或触发重新取数）
      if (!isVersionConflict(error) && context?.previous) {
        queryClient.setQueryData(planKeys.detail(planId), context.previous);
      }
      onError(error);
    },
    onSuccess: (result) => {
      queryClient.setQueryData<PlanRead>(
        planKeys.detail(planId),
        withRecomputedProgress(sortTree(result.plan)),
      );
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
    },
  });
}

/**
 * 计划级改名 / 归档 / 换目标：返回 `PlanRead`（不是 `PlanMutationResult`）。
 * `toPatch` 把页面侧的简单入参（标题、状态）翻成 `PlanPatch`，version 由缓存补。
 */
function usePlanPatchMutation<TVariables>(
  planId: number,
  toPatch: (variables: TVariables) => Omit<PlanPatch, "version">,
) {
  const queryClient = useQueryClient();
  const onError = usePlanErrorHandler(planId);

  return useMutation<PlanRead, unknown, TVariables, MutateContext>({
    mutationFn: (variables) =>
      patchPlan(planId, {
        ...toPatch(variables),
        version: currentPlanVersion(queryClient, planId),
      }),
    onMutate: async (variables) => {
      const patch = toPatch(variables);
      await queryClient.cancelQueries({ queryKey: planKeys.detail(planId) });
      const previous = queryClient.getQueryData<PlanRead>(planKeys.detail(planId));
      if (previous) {
        queryClient.setQueryData<PlanRead>(planKeys.detail(planId), {
          ...previous,
          ...(patch.title != null ? { title: patch.title } : {}),
          ...(patch.goal != null ? { goal: patch.goal } : {}),
          ...(patch.status != null ? { status: patch.status } : {}),
        });
      }
      return { previous };
    },
    onError: (error, _variables, context) => {
      if (!isVersionConflict(error) && context?.previous) {
        queryClient.setQueryData(planKeys.detail(planId), context.previous);
      }
      onError(error);
    },
    onSuccess: (result) => {
      queryClient.setQueryData<PlanRead>(planKeys.detail(planId), result);
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
    },
  });
}

export interface PlanMutations {
  rename: UseMutationResult<PlanRead, unknown, string, MutateContext>;
  setStatus: UseMutationResult<PlanRead, unknown, PlanStatus, MutateContext>;
  addStage: UseMutationResult<PlanMutationResult, unknown, StageDraftInput, MutateContext>;
  renameStage: UseMutationResult<PlanMutationResult, unknown, RenameStageInput, MutateContext>;
  moveStage: UseMutationResult<PlanMutationResult, unknown, MoveStageInput, MutateContext>;
  removeStage: UseMutationResult<PlanMutationResult, unknown, number, MutateContext>;
  addItem: UseMutationResult<PlanMutationResult, unknown, AddItemInput, MutateContext>;
  patchItem: UseMutationResult<PlanMutationResult, unknown, PatchItemInput, MutateContext>;
  removeItem: UseMutationResult<PlanMutationResult, unknown, ItemRef, MutateContext>;
}

export interface StageDraftInput {
  title: string;
  weeks?: number;
  description?: string;
}

export interface RenameStageInput {
  stageId: number;
  title: string;
}

export interface MoveStageInput {
  stageId: number;
  orderIndex: number;
}

export interface AddItemInput {
  stageId: number;
  title: string;
  estMinutes?: number;
  resourceSlug?: string | null;
  note?: string;
}

export interface PatchItemInput {
  itemId: number;
  patch: Omit<ItemPatch, "version">;
}

export interface ItemRef {
  itemId: number;
  version: number;
}

/**
 * 条目的本地改动（含**跨阶段拖拽**）：`patch.stage_id` 有值就把条目搬到目标阶段，
 * 否则原地改字段。两个动作共用一份逻辑，避免"改字段"和"换阶段"写成两套数组操作。
 */
function applyItemPatch(plan: PlanRead, input: PatchItemInput): PlanRead {
  const source = plan.stages
    .flatMap((stage) => stage.items)
    .find((item) => item.id === input.itemId);
  if (!source) return plan;

  const targetStageId = input.patch.stage_id ?? null;
  const patched = { ...source, ...toItemFields(input.patch) };

  const stages = plan.stages.map((stage) => {
    const without = stage.items.filter((item) => item.id !== input.itemId);
    if (targetStageId !== null && stage.id === targetStageId) {
      return { ...stage, items: [...without, { ...patched, stage_id: stage.id }] };
    }
    if (targetStageId === null && stage.id === source.stage_id) {
      return { ...stage, items: [...without, patched] };
    }
    if (stage.id === source.stage_id) {
      return { ...stage, items: without };
    }
    return stage;
  });

  return { ...plan, stages };
}

/** `ItemPatch` → 条目字段（只映射真正传了的键；`resource_slug: null` 是解绑，必须保留）。 */
function toItemFields(patch: Omit<ItemPatch, "version">): Partial<ItemRead> {
  const fields: Partial<ItemRead> = {};
  if (patch.title != null) fields.title = patch.title;
  if (patch.note != null) fields.note = patch.note;
  if (patch.est_minutes != null) fields.est_minutes = patch.est_minutes;
  if (patch.status != null) fields.status = patch.status;
  if (patch.resource_slug !== undefined) fields.resource_slug = patch.resource_slug;
  if (patch.order_index != null) fields.order_index = patch.order_index;
  return fields;
}

/** 乐观更新用的占位 id：负数，永远不会和真实自增主键撞车。 */
let placeholderSeq = 0;
function nextPlaceholderId(): number {
  placeholderSeq += 1;
  return -placeholderSeq;
}

/**
 * 计划树的一组写操作（§8.3）。`planId` 必须有效，否则 hook 不该被调用。
 *
 * 每个 mutation 的 `variables` 与 API 形状保持一致；`version` 由这里从缓存里读
 * （乐观态不碰 version，所以读到的就是服务端真值）。
 */
export function usePlanMutations(planId: number): PlanMutations {
  const queryClient = useQueryClient();

  const planVersion = useCallback(
    () => currentPlanVersion(queryClient, planId),
    [queryClient, planId],
  );

  const addStageMutation = usePlanTreeMutation<StageDraftInput>(
    planId,
    (plan, input) => {
      const orderIndex =
        plan.stages.reduce((max, stage) => Math.max(max, stage.order_index), 0) + 1000;
      const stage: StageRead = {
        id: nextPlaceholderId(),
        plan_id: plan.id,
        title: input.title,
        description: input.description ?? "",
        weeks: input.weeks ?? 1,
        order_index: orderIndex,
        items: [],
        progress: null,
      };
      return { ...plan, stages: [...plan.stages, stage] };
    },
    (input) =>
      addStage(planId, {
        title: input.title,
        description: input.description ?? "",
        weeks: input.weeks ?? 1,
        order_index: null,
        version: planVersion(),
      }),
  );

  const renameStageMutation = usePlanTreeMutation<RenameStageInput>(
    planId,
    (plan, input) => ({
      ...plan,
      stages: plan.stages.map((stage) =>
        stage.id === input.stageId ? { ...stage, title: input.title } : stage,
      ),
    }),
    (input) => patchStage(input.stageId, { title: input.title, version: planVersion() }),
  );

  const moveStageMutation = usePlanTreeMutation<MoveStageInput>(
    planId,
    (plan, input) => ({
      ...plan,
      stages: plan.stages.map((stage) =>
        stage.id === input.stageId ? { ...stage, order_index: input.orderIndex } : stage,
      ),
    }),
    (input) =>
      patchStage(input.stageId, {
        order_index: input.orderIndex,
        version: planVersion(),
      }),
  );

  // 删阶段是硬删除且连带条目：传进来的 version 是计划的（D4）
  const removeStageMutation = usePlanTreeMutation<number>(
    planId,
    (plan, stageId) => ({
      ...plan,
      stages: plan.stages.filter((stage) => stage.id !== stageId),
    }),
    (stageId) => deleteStage(stageId, planVersion()),
  );

  const addItemMutation = usePlanTreeMutation<AddItemInput>(
    planId,
    (plan, input) => ({
      ...plan,
      stages: plan.stages.map((stage) => {
        if (stage.id !== input.stageId) return stage;
        const orderIndex =
          stage.items.reduce((max, item) => Math.max(max, item.order_index), 0) + 1000;
        return {
          ...stage,
          items: [
            ...stage.items,
            {
              id: nextPlaceholderId(),
              stage_id: stage.id,
              title: input.title,
              resource_slug: input.resourceSlug ?? null,
              note: input.note ?? "",
              est_minutes: input.estMinutes ?? 30,
              order_index: orderIndex,
              status: "todo",
              depends_on: null,
              completed_at: null,
              version: 1,
              is_deleted: 0,
              resolved_title: null,
              resource_available: input.resourceSlug ? true : false,
            },
          ],
        };
      }),
    }),
    (input) =>
      addPlanItem({
        stage_id: input.stageId,
        title: input.title,
        resource_slug: input.resourceSlug ?? null,
        note: input.note ?? "",
        est_minutes: input.estMinutes ?? 30,
        order_index: null,
        version: planVersion(),
      }),
  );

  const patchItemMutation = usePlanTreeMutation<PatchItemInput>(
    planId,
    applyItemPatch,
    (input) =>
      patchPlanItem(input.itemId, {
        ...input.patch,
        version: itemVersionOf(queryClient, planId, input.itemId),
      }),
  );

  const removeItemMutation = usePlanTreeMutation<ItemRef>(
    planId,
    (plan, ref) => ({
      ...plan,
      stages: plan.stages.map((stage) => ({
        ...stage,
        items: stage.items.filter((item) => item.id !== ref.itemId),
      })),
    }),
    (ref) => deletePlanItem(ref.itemId, ref.version),
  );

  return {
    rename: usePlanPatchMutation<string>(planId, (title) => ({ title })),
    setStatus: usePlanPatchMutation<PlanStatus>(planId, (status) => ({ status })),
    addStage: addStageMutation,
    renameStage: renameStageMutation,
    moveStage: moveStageMutation,
    removeStage: removeStageMutation,
    addItem: addItemMutation,
    patchItem: patchItemMutation,
    removeItem: removeItemMutation,
  };
}

/* ------------------------------------------------------- 列表页 / 新建页 */

export interface PlanListActions {
  rename: UseMutationResult<PlanRead, unknown, { planId: number; title: string }>;
  setStatus: UseMutationResult<PlanRead, unknown, { planId: number; status: PlanStatus }>;
  remove: UseMutationResult<PlanRead, unknown, { planId: number; version: number }>;
  duplicate: UseMutationResult<PlanRead, unknown, PlanRead>;
}

function versionOf(queryClient: QueryClient, planId: number): number {
  const cached = queryClient.getQueryData<PlanRead>(planKeys.detail(planId));
  if (cached) return cached.version;
  const lists = queryClient.getQueriesData<PlanListResponse>({ queryKey: planKeys.all });
  for (const [, data] of lists) {
    const found = data?.items.find((item) => item.id === planId);
    if (found) return found.version;
  }
  return 1;
}

/**
 * 列表页对「非当前打开的某个计划」的操作：没有 `["plan", id]` 的乐观态可改，
 * 成功后统一 `invalidate(["plans"])`；409 同样走 `handlePlanError`。
 */
export function usePlanListActions(): PlanListActions {
  const queryClient = useQueryClient();
  const onError = usePlanErrorHandler(null);

  const rename = useMutation<PlanRead, unknown, { planId: number; title: string }>({
    mutationFn: ({ planId, title }) =>
      patchPlan(planId, { title, version: versionOf(queryClient, planId) }),
    onSuccess: (plan) => {
      queryClient.setQueryData<PlanRead>(planKeys.detail(plan.id), plan);
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
    },
    onError,
  });

  const setStatus = useMutation<PlanRead, unknown, { planId: number; status: PlanStatus }>({
    mutationFn: ({ planId, status }) =>
      patchPlan(planId, { status, version: versionOf(queryClient, planId) }),
    onSuccess: (plan) => {
      queryClient.setQueryData<PlanRead>(planKeys.detail(plan.id), plan);
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
    },
    onError,
  });

  const remove = useMutation<PlanRead, unknown, { planId: number; version: number }>({
    mutationFn: ({ planId, version }) => deletePlan(planId, version),
    onSuccess: (plan) => {
      // 软删除后详情恒 404：把详情缓存摘掉，避免列表页残留
      queryClient.removeQueries({ queryKey: planKeys.detail(plan.id) });
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
    },
    onError,
  });

  const duplicate = useMutation<PlanRead, unknown, PlanRead>({
    mutationFn: (plan) =>
      createPlan({
        draft: draftFromPlan(plan),
        origin: "manual",
        template_id: null,
        start_level: plan.start_level,
        set_active: false,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
    },
    onError,
  });

  return { rename, setStatus, remove, duplicate };
}

/** `PlanRead` → `PlanDraft`（复制为新计划 / 重新生成时共用）。 */
export function draftFromPlan(plan: PlanRead, title?: string): PlanDraft {
  return {
    title: (title ?? `${plan.title} 副本`).slice(0, 60),
    goal: plan.goal,
    weeks: plan.horizon_weeks,
    weekly_hours: plan.weekly_hours,
    stages: plan.stages.map((stage) => ({
      title: stage.title,
      weeks: stage.weeks,
      description: stage.description,
      items: stage.items.map((item) => ({
        title: item.title,
        resource_slug: item.resource_slug,
        est_minutes: item.est_minutes,
        note: item.note,
      })),
    })),
  };
}

/** 新建计划页的保存动作（`POST /api/plans` → 201 `PlanRead`）。 */
export function useCreatePlan(): UseMutationResult<PlanRead, unknown, PlanCreate> {
  const queryClient = useQueryClient();
  const toastApi = useToast();
  const { t } = useTranslation("plans");

  return useMutation<PlanRead, unknown, PlanCreate>({
    mutationFn: createPlan,
    onSuccess: (plan) => {
      queryClient.setQueryData<PlanRead>(planKeys.detail(plan.id), plan);
      void queryClient.invalidateQueries({ queryKey: planKeys.all });
      toastApi.push({ kind: "success", message: t("toast.created") });
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        pushToast({ kind: "error", message: errorMessage(error.code), hint: error.hint });
        return;
      }
      pushToast({ kind: "error", message: errorMessage("INTERNAL") });
    },
  });
}

/* --------------------------------------------------- 模板 / 草稿工具 */

/**
 * 把模板草稿（或空白草稿）复制成可编辑的本地状态。
 * 预览编辑页**不写库**：所有改动都留在这个函数的返回值里（§8.2 第 5 步）。
 */
export function cloneDraft(draft: PlanDraft): PlanDraft {
  return {
    ...draft,
    stages: draft.stages.map((stage) => ({
      ...stage,
      items: stage.items.map((item) => ({ ...item })),
    })),
  };
}

/** 空白计划的最小合法形状（§3.10：阶段 2–8、每阶段 1–20 条、总条目 5–40）。 */
export function blankDraft(): PlanDraft {
  return skeletonDraft({ title: "", goal: "", weeks: 12, weeklyHours: 8 });
}

/** 表单 → 草稿的骨架（AI 生成路径 M4 落地前，用表单值直接搭一份可编辑草稿）。 */
export function draftFromForm(form: {
  goal: string;
  level?: string;
  weeklyHours: number;
  weeks: number;
}): PlanDraft {
  const goal = form.goal.trim();
  return skeletonDraft({
    title: (goal.length > 0 ? goal : "新学习计划").slice(0, 60),
    goal: goal.slice(0, 120),
    weeks: form.weeks,
    weeklyHours: form.weeklyHours,
  });
}

/**
 * 4 阶段 × 5 条的骨架，**时长按预算反算**。
 *
 * 必须这么做：§3.10 第 4 条要求总时长与 `horizon_weeks × weekly_hours × 60`
 * 偏差 ≤30%，随手写 30 分钟会让 `POST /api/plans` 直接 422 `PLAN_INVALID`
 * （实测：5 条 ×30 分钟、12 周 ×8 小时 → 偏差 97%）。
 */
function skeletonDraft(input: {
  title: string;
  goal: string;
  weeks: number;
  weeklyHours: number;
}): PlanDraft {
  const weeks = Math.min(52, Math.max(4, Math.round(input.weeks)));
  const weeklyHours = Math.min(40, Math.max(1, Math.round(input.weeklyHours)));
  const stageTitles = ["基础", "进阶", "实战", "复盘"];
  const perStage = 5;
  const itemCount = stageTitles.length * perStage;
  const budget = weeks * weeklyHours * 60;
  const each = Math.min(240, Math.max(10, Math.round(budget / itemCount / 5) * 5));
  const baseWeeks = Math.max(1, Math.floor(weeks / stageTitles.length));

  return {
    title: input.title,
    goal: input.goal,
    weeks,
    weekly_hours: weeklyHours,
    stages: stageTitles.map((title, index) => ({
      title,
      // 最后一阶段吃掉除不尽的周数，避免各阶段周数之和与 weeks 差太多（§3.10 第 3 条）
      weeks:
        index === stageTitles.length - 1
          ? Math.max(1, weeks - baseWeeks * (stageTitles.length - 1))
          : baseWeeks,
      description: "",
      items: Array.from({ length: perStage }, () => ({
        title: "自定义任务",
        resource_slug: null,
        est_minutes: each,
        note: "",
      })),
    })),
  };
}

/** 阶段大纲（桌面右侧）要用：阶段 id → 标题 + 进度。 */
export function stageOutline(plan: PlanRead): { id: number; title: string; percent: number }[] {
  return plan.stages.map((stage) => ({
    id: stage.id,
    title: stage.title,
    percent: stage.progress?.percent ?? 0,
  }));
}

/** 深链接：`/plans/:planId/items/:slug` 的返回地址。 */
export function planItemHref(planId: number, slug: string): string {
  return `/plans/${planId}/items/${slug}`;
}

/** 排序防抖（§8.3 最后一条：300ms）：同一 key 连续触发只提交最后一次。 */
export function useDebouncedCommit(delayMs = 300): {
  schedule: (key: string, run: () => void) => void;
  flush: () => void;
  cancel: () => void;
} {
  const timers = useMemo(() => new Map<string, number>(), []);

  const flush = useCallback(() => {
    for (const timer of timers.values()) {
      window.clearTimeout(timer);
    }
    timers.clear();
  }, [timers]);

  const schedule = useCallback(
    (key: string, run: () => void) => {
      const existing = timers.get(key);
      if (existing !== undefined) window.clearTimeout(existing);
      const timer = window.setTimeout(() => {
        timers.delete(key);
        run();
      }, delayMs);
      timers.set(key, timer);
    },
    [delayMs, timers],
  );

  return { schedule, flush, cancel: flush };
}
