/**
 * 设置页数据层（Owner：打磨模块 M5，`docs/contracts/MODULES.md` §3.3）。
 *
 * 契约里点名的四个 hook 都在这里，query key 也按契约固定：
 * `["settings"]`、`["index-status", taskId]`；`usePrivateScan` 成功后写
 * `["taxonomy"]` 与 `["resources"]`（私有资料进的是同一个 resources 表）。
 *
 * 三个实现口径（改之前先读）：
 * 1. **轮询只在 `running` 时开**：`IndexRunState` 没有 `done`，终态是
 *    `idle && percent===100`；`refetchInterval` 返回 `false` 就停，别用固定 interval
 *    让页面在空闲时也一直打 `/api/index/status`。
 * 2. **409 `INDEX_BUILDING` 不是失败**：后端在 `detail` 里带了正在跑的 `task_id`，
 *    页面接住它继续轮询即可（见 `pages/settings` 的 `handleRebuild`）。
 * 3. **写操作不回填输入框**：`PUT` 的响应只用来更新缓存与掩码，页面上的表单状态
 *    由用户输入驱动，避免"保存中"被服务端回显覆盖掉正在编辑的值。
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { getIndexStatus, rebuildIndex, rescanPrivate } from "@/lib/api/index";
import { getSettings, putSettings, testSettings } from "@/lib/api/settings";
import type {
  IndexRebuildRequest,
  IndexRebuildResponse,
  IndexStatus,
  PrivateRescanResponse,
  SettingsRead,
  SettingsTestRequest,
  SettingsTestResponse,
  SettingsWrite,
} from "@/types/api";

/* ------------------------------------------------------------------ keys */

export const settingsKeys = {
  all: ["settings"] as const,
  indexStatus: (taskId: string | null) => ["index-status", taskId] as const,
};

/* ------------------------------------------------------------------ 读 */

/** `GET /api/settings`；密钥只出掩码。 */
export function useSettings(): UseQueryResult<SettingsRead> {
  return useQuery({
    queryKey: settingsKeys.all,
    queryFn: getSettings,
  });
}

/**
 * `GET /api/index/status`；`running` 时 1s 轮询，其余状态停下。
 *
 * `taskId` 传 `null` 表示"还没发起过重建"，此时不发请求（后端会把 `task_id` 省略
 * 当成"最近一次任务"，空闲页不该为了显示而多打一次接口）。
 */
export function useIndexStatus(taskId: string | null): UseQueryResult<IndexStatus> {
  return useQuery({
    queryKey: settingsKeys.indexStatus(taskId),
    queryFn: () => getIndexStatus(taskId),
    enabled: taskId !== null,
    // 轮询期间别把 1s 一次的结果当 stale 又去重取
    staleTime: 0,
    refetchInterval: (query) => {
      const status = query.state.data as IndexStatus | undefined;
      if (!status) return 1_000;
      if (status.state === "running") return 1_000;
      // idle / failed 都是终态：停轮询，用户在页面上看到的是最终值
      return false;
    },
  });
}

/* ------------------------------------------------------------------ 写 */

/** `PUT /api/settings`：局部更新，成功即用响应覆盖 `["settings"]`。 */
export function useSaveSettings(): UseMutationResult<SettingsRead, unknown, SettingsWrite> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: putSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(settingsKeys.all, data);
      // 健康探针里的 llm_configured / embedding_configured 会跟着变
      void queryClient.invalidateQueries({ queryKey: ["health"] });
    },
  });
}

/** `POST /api/settings/test`：探针失败也是 200，`ok=false` 走正常返回。 */
export function useTestConnection(): UseMutationResult<
  SettingsTestResponse,
  unknown,
  SettingsTestRequest
> {
  return useMutation({ mutationFn: testSettings });
}

/** `POST /api/index/rebuild`：202 / 409 / 429 都是"有信息可用的结果"，交给页面分支。 */
export function useRebuildIndex(): UseMutationResult<
  IndexRebuildResponse,
  unknown,
  IndexRebuildRequest
> {
  return useMutation({ mutationFn: (body: IndexRebuildRequest) => rebuildIndex(body) });
}

/** `POST /api/private/rescan`：成功后私有资料会出现在资料库里，所以失效那两个 key。 */
export function usePrivateScan(): UseMutationResult<PrivateRescanResponse, unknown, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: rescanPrivate,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["taxonomy"] });
      void queryClient.invalidateQueries({ queryKey: ["resources"] });
    },
  });
}
