/**
 * 计划内阅读用的资料加载（Owner：计划模块 M3）。
 *
 * `getResource` 的函数归属是**资料页（M2）**（MODULES §3.2），所以这里只做一个薄 hook：
 * 包一层 `useQuery` 拿到页面要的 loading/error 状态，端点与传输细节全部复用 M2 的实现。
 * slug 含 `/`（D2），由 `getResource` 自己负责拼路径，本模块不重复编码。
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getResource } from "@/lib/api/resources";
import type { ResourceDetail } from "@/types/api";

export function usePlanResource(
  slug: string | null | undefined,
): UseQueryResult<ResourceDetail> {
  return useQuery({
    queryKey: ["plan-resource", slug ?? ""],
    queryFn: () => getResource(slug as string),
    enabled: !!slug,
    staleTime: 60_000,
  });
}
