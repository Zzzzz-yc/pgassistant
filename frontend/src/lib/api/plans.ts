/**
 * 计划模块 API client（Owner：计划模块 M3，docs/contracts/MODULES.md §3.2）。
 *
 * 只做「路径 + 方法 + query/body 的搬运」：不缓存、不弹 Toast、不改 version。
 * 语义与端点逐条对照 `docs/contracts/API.md` §3.8–§3.15：
 * - 创建类（POST /api/plans、POST .../stages、POST /api/plan-items）**不带** version（D4）；
 * - 修改类把 version 放**请求体**；
 * - 删除类把 version 放 **query** `?version=`（D4：DELETE 带 body 会被代理丢弃）。
 */
import { request } from "@/lib/http";
import type {
  ItemCreate,
  ItemPatch,
  PlanCreate,
  PlanDraft,
  PlanListQuery,
  PlanListResponse,
  PlanMutationResult,
  PlanPatch,
  PlanRead,
  PlanTemplateListResponse,
  StageCreate,
  StagePatch,
} from "@/types/api";

/** `GET /api/plans?status=`（不分页，API.md §3.8）。 */
export function listPlans(params: PlanListQuery = {}): Promise<PlanListResponse> {
  return request<PlanListResponse>("GET", "/api/plans", {
    query: { status: params.status },
  });
}

/** `POST /api/plans` → 201 `PlanRead`（含 `id` 与 `version: 1`）。 */
export function createPlan(body: PlanCreate): Promise<PlanRead> {
  return request<PlanRead>("POST", "/api/plans", { body });
}

/** `GET /api/plans/{id}`；已软删除 → 404 `PLAN_NOT_FOUND`。 */
export function getPlan(id: number): Promise<PlanRead> {
  return request<PlanRead>("GET", `/api/plans/${id}`);
}

/** `PATCH /api/plans/{id}` — 改名 / 换目标 / 归档 / 激活（version 在 body）。 */
export function patchPlan(id: number, body: PlanPatch): Promise<PlanRead> {
  return request<PlanRead>("PATCH", `/api/plans/${id}`, { body });
}

/** `DELETE /api/plans/{id}?version=` — 软删除，响应 `is_deleted = 1`。 */
export function deletePlan(id: number, version: number): Promise<PlanRead> {
  return request<PlanRead>("DELETE", `/api/plans/${id}`, { query: { version } });
}

/** `POST /api/plans/{id}/stages` → 201 `PlanMutationResult`（version = plans.version）。 */
export function addStage(planId: number, body: StageCreate): Promise<PlanMutationResult> {
  return request<PlanMutationResult>("POST", `/api/plans/${planId}/stages`, { body });
}

/** `PATCH /api/stages/{id}` —— version 是**所属计划**的（D4）。 */
export function patchStage(stageId: number, body: StagePatch): Promise<PlanMutationResult> {
  return request<PlanMutationResult>("PATCH", `/api/stages/${stageId}`, { body });
}

/** `DELETE /api/stages/{id}?version=` — 硬删除，连带条目（D9 第 4 条）。 */
export function deleteStage(stageId: number, version: number): Promise<PlanMutationResult> {
  return request<PlanMutationResult>("DELETE", `/api/stages/${stageId}`, {
    query: { version },
  });
}

/** `POST /api/plan-items` → 201 `PlanMutationResult`。 */
export function addPlanItem(body: ItemCreate): Promise<PlanMutationResult> {
  return request<PlanMutationResult>("POST", "/api/plan-items", { body });
}

/**
 * `PATCH /api/plan-items/{id}` —— version 是**条目**的（D4）。
 * 注意 `resource_slug: null` 是「显式解绑」，字段缺席才是「不改」；
 * 因此调用方必须自己保证「不想改就不放这个键」，这里不做 `undefined` 剔除之外的加工。
 */
export function patchPlanItem(
  itemId: number,
  body: ItemPatch,
): Promise<PlanMutationResult> {
  return request<PlanMutationResult>("PATCH", `/api/plan-items/${itemId}`, { body });
}

/** `DELETE /api/plan-items/{id}?version=` — 软删除，条目从 plan 里消失。 */
export function deletePlanItem(
  itemId: number,
  version: number,
): Promise<PlanMutationResult> {
  return request<PlanMutationResult>("DELETE", `/api/plan-items/${itemId}`, {
    query: { version },
  });
}

/** `GET /api/plan-templates`（4 份模板的摘要，不带阶段明细）。 */
export function listPlanTemplates(): Promise<PlanTemplateListResponse> {
  return request<PlanTemplateListResponse>("GET", "/api/plan-templates");
}

/** `GET /api/plan-templates/{id}` → `PlanDraft`，可直接进预览编辑页。 */
export function getPlanTemplate(templateId: string): Promise<PlanDraft> {
  return request<PlanDraft>(
    "GET",
    `/api/plan-templates/${encodeURIComponent(templateId)}`,
  );
}
