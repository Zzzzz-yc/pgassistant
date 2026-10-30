/**
 * 资料页 hooks（Owner：资料页 M2；`docs/contracts/MODULES.md` §3.3）。
 *
 * 约定：
 * - query key 与契约表逐字一致（`["resources", params]` / `["resource", slug]` /
 *   `["related", slug]` / `["taxonomy"]` / `["plans", params]` / `["plan", id]`），
 *   这样计划模块的写操作只要 `invalidateQueries(["plans"])` 就能连带刷新我这边；
 * - 查询的重试策略由 `lib/query.ts` 的全局默认（1 次）决定，这里不覆盖；
 * - **没有** `["search"]` 缓存：搜索是命令式的（用户回车才发），用 `useSearch` 的本地状态。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { addPlanItem, getPlan, listPlans } from "@/lib/api/plans";
import { getRelated, getResource, getTaxonomy, listResources } from "@/lib/api/resources";
import { searchGet, searchPost } from "@/lib/api/search";
import { ApiError } from "@/lib/http";
import type {
  PlanListResponse,
  PlanMutationResult,
  PlanRead,
  RelatedResponse,
  ResourceDetail,
  ResourceListQuery,
  ResourceListResponse,
  SearchQuery,
  SearchResponse,
  TaxonomyResponse,
} from "@/types/api";

/* ----------------------------------------------------------- 只读：资料 */

/** 分类树。key 与 `lib/query.ts` 的 `useTaxonomy` 相同，缓存共用。 */
export function useTaxonomyData(): UseQueryResult<TaxonomyResponse, ApiError> {
  return useQuery<TaxonomyResponse, ApiError>({ queryKey: ["taxonomy"], queryFn: getTaxonomy });
}

export function useResourceList(
  params: ResourceListQuery,
): UseQueryResult<ResourceListResponse, ApiError> {
  return useQuery<ResourceListResponse, ApiError>({
    queryKey: ["resources", params],
    queryFn: () => listResources(params),
  });
}

export function useResource(slug: string | undefined): UseQueryResult<ResourceDetail, ApiError> {
  return useQuery<ResourceDetail, ApiError>({
    queryKey: ["resource", slug],
    queryFn: () => getResource(slug as string),
    enabled: Boolean(slug),
  });
}

export function useRelated(slug: string | undefined): UseQueryResult<RelatedResponse, ApiError> {
  return useQuery<RelatedResponse, ApiError>({
    queryKey: ["related", slug],
    queryFn: () => getRelated(slug as string),
    enabled: Boolean(slug),
  });
}

/* --------------------------------------------------------- 命令式：检索 */

export interface UseSearchResult {
  data: SearchResponse | null;
  error: ApiError | null;
  loading: boolean;
  /** 发一次检索；`mode="post"` 走命令面板那条（body），默认 `get`。 */
  run: (
    query: string,
    options?: { mode?: "get" | "post"; limit?: number; filters?: Omit<SearchQuery, "q"> },
  ) => Promise<SearchResponse | null>;
  reset: () => void;
}

/**
 * 命令式检索（`MODULES.md` §3.3 的 `useSearch`）。
 *
 * 与契约表的一处差别：`params` 是**默认参数**（筛选 / limit），每次 `run(q)` 时合并进去；
 * 检索不缓存（用户回车才发），所以没有 `["search", params]` 这个 query key —— 需求文档
 * §9.9 的“零结果三动作”要的是“发一次、拿到这次结果”，缓存反而会让二次点击看到旧数据。
 */
export function useSearch(
  defaults: Omit<SearchQuery, "q"> & { mode?: "get" | "post" } = {},
): UseSearchResult {
  const [data, setData] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const current = useRef(0);
  const base = useRef(defaults);
  base.current = defaults;

  const run = useCallback<UseSearchResult["run"]>(async (query, options = {}) => {
    const text = query.trim();
    if (!text) return null;
    const ticket = ++current.current;
    const filters = { ...base.current, ...(options.filters ?? {}) };
    const mode = options.mode ?? base.current.mode ?? "get";
    setLoading(true);
    setError(null);
    try {
      const result =
        mode === "post"
          ? await searchPost({ q: text, limit: options.limit ?? 8, ...filters })
          : await searchGet({ q: text, limit: options.limit ?? 10, ...filters });
      if (ticket !== current.current) return null;      // 后发先至：丢掉过期结果
      setData(result);
      return result;
    } catch (cause) {
      if (ticket !== current.current) return null;
      setError(
        cause instanceof ApiError
          ? cause
          : new ApiError("NETWORK_ERROR", "连不上本地服务", 0, { detail: String(cause) }),
      );
      setData(null);
      return null;
    } finally {
      if (ticket === current.current) setLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    current.current += 1;
    setData(null);
    setError(null);
    setLoading(false);
  }, []);

  return { data, error, loading, run, reset };
}

/* -------------------------------------------------------------- 计划联动 */

/**
 * 当前激活计划（列表接口，含 `active_plan_id`）。
 *
 * 计划模块的 `lib/api/plans.ts` 是本文件唯一的外部依赖；它交付前这里用的是同一个
 * `request()` 直调，交付后换成契约函数，行为不变（父 Agent 已确认无需回改）。
 */
export function useActivePlan(): UseQueryResult<PlanListResponse, ApiError> {
  return useQuery<PlanListResponse, ApiError>({
    queryKey: ["plans", { status: "active" }],
    queryFn: () => listPlans({ status: "active" }),
  });
}

/** 激活计划的整棵树（「继续学习」要条目，`listPlans` 只有摘要）。 */
export function usePlanDetail(planId: number | null | undefined): UseQueryResult<PlanRead, ApiError> {
  return useQuery<PlanRead, ApiError>({
    queryKey: ["plan", planId],
    queryFn: () => getPlan(planId as number),
    enabled: typeof planId === "number" && planId > 0,
  });
}

export interface AddToPlanInput {
  planId: number;
  stageId: number;
  version: number;
  slug: string;
  title: string;
  estMinutes?: number;
}

/**
 * 「加入计划」：`POST /api/plan-items`。
 *
 * 响应是整棵 `PlanMutationResult`（D6），所以成功后就地把 `["plan", id]` 覆盖掉，
 * 再让 `["plans"]` 失效——与计划模块的缓存失效规则一致（`MODULES.md` §3.3）。
 */
export function useAddToPlan(): UseMutationResult<PlanMutationResult, ApiError, AddToPlanInput> {
  const queryClient = useQueryClient();
  return useMutation<PlanMutationResult, ApiError, AddToPlanInput>({
    mutationFn: (input) =>
      addPlanItem({
        stage_id: input.stageId,
        title: input.title,
        resource_slug: input.slug,
        est_minutes: input.estMinutes ?? 30,
        version: input.version,
      }),
    onSuccess: (result, input) => {
      queryClient.setQueryData(["plan", input.planId], result.plan);
      void queryClient.invalidateQueries({ queryKey: ["plans"] });
    },
  });
}

/* --------------------------------------------------------------- 滚动恢复 */

const SCROLL_PREFIX = "pg_scroll_";

/**
 * 真正的滚动容器。
 *
 * 布局层（`styles/layout.css` 的 `.app-shell` / `.app-main`）在内容不高时让 `.app-main`
 * 撑满而不产生内部滚动，此时滚的是**文档**（`document.scrollingElement`）。两种都要支持，
 * 否则滚动恢复、目录高亮、翻页回到顶部全都会作用在一个不会滚的元素上。
 */
export function getScrollContainer(): HTMLElement | null {
  const doc = document.scrollingElement as HTMLElement | null;
  if (doc && doc.scrollHeight > doc.clientHeight + 1) return doc;
  const main = document.querySelector<HTMLElement>(".app-main");
  if (main && main.scrollHeight > main.clientHeight + 1) return main;
  return doc ?? document.documentElement;
}

function readScrollTop(container: HTMLElement): number {
  if (container === document.scrollingElement) return window.scrollY || container.scrollTop;
  return container.scrollTop;
}

function writeScrollTop(container: HTMLElement, top: number): void {
  if (container === document.scrollingElement) window.scrollTo(0, top);
  else container.scrollTop = top;
}

/**
 * 返回资料库时恢复滚动位置（§9.5：sessionStorage 记 scrollTop + URL 带 page）。
 *
 * 骨架屏与真实网格同尺寸（§9.6），所以挂载后先恢复一次；若内容还在加载导致
 * `scrollTop` 被夹回去，就在 3 帧内重试，直到到位或放弃（不会无限循环）。
 *
 * 监听挂在 `window` 的**捕获阶段**：元素上的 `scroll` 不冒泡，但捕获阶段能看到，
 * 这样不用猜滚动到底发生在 `.app-main` 还是文档上。
 */
export function useScrollRestore(key: string): void {
  useEffect(() => {
    const container = getScrollContainer();
    if (!container) return;

    const storageKey = SCROLL_PREFIX + key;
    let saved = 0;
    try {
      saved = Number(window.sessionStorage.getItem(storageKey) ?? "0") || 0;
    } catch {
      saved = 0;
    }

    let frames = 0;
    let frameId = 0;
    const restore = () => {
      if (saved <= 0) return;
      writeScrollTop(container, saved);
      frames += 1;
      if (Math.abs(readScrollTop(container) - saved) > 2 && frames < 3) {
        frameId = window.requestAnimationFrame(restore);
      }
    };
    frameId = window.requestAnimationFrame(restore);

    let timer = 0;
    const save = () => {
      try {
        window.sessionStorage.setItem(storageKey, String(readScrollTop(container)));
      } catch {
        // 隐私模式写不进去：只是回到列表后位置在顶部，不影响功能
      }
    };
    const onScroll = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(save, 120);
    };

    window.addEventListener("scroll", onScroll, { capture: true, passive: true });
    return () => {
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(timer);
      window.removeEventListener("scroll", onScroll, { capture: true });
      save();
    };
  }, [key]);
}

/* ---------------------------------------------------------------- 首次引导 */

export const ONBOARDED_KEY = "onboarded_v1";
const ONBOARDED_EVENT = "pg:onboarded";

function readOnboarded(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDED_KEY) === "1";
  } catch {
    return false;      // 读不到就当作"已看过"，避免每次刷新都弹引导带
  }
}

/** 首次引导带（§9.9）：点任意按钮或关闭即写 `onboarded_v1`，永不再现。 */
export function useOnboarded(): { seen: boolean; dismiss: () => void } {
  const [seen, setSeen] = useState<boolean>(() => readOnboarded());

  useEffect(() => {
    const onChange = () => setSeen(readOnboarded());
    window.addEventListener(ONBOARDED_EVENT, onChange);
    return () => window.removeEventListener(ONBOARDED_EVENT, onChange);
  }, []);

  const dismiss = useCallback(() => {
    try {
      window.localStorage.setItem(ONBOARDED_KEY, "1");
    } catch {
      // 存不下也不影响本次会话：状态照样切换
    }
    setSeen(true);
    window.dispatchEvent(new Event(ONBOARDED_EVENT));
  }, []);

  return { seen, dismiss };
}
