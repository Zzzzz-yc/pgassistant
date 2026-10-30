/**
 * 检索域 API client（Owner：资料页 M2；`docs/contracts/MODULES.md` §3.2）。
 *
 * `GET` 与 `POST` **语义与响应结构完全一致**（DECISIONS D1），只是参数位置不同：
 * - `searchGet`：资料库搜索框、URL 可分享的查询；
 * - `searchPost`：命令面板（`components/business/CommandPalette` 默认实现已有内联版本，
 *   页面需要注入时用这个函数，保持 `took_ms` 与错误信封一致）。
 */
import { request } from "@/lib/http";
import type { SearchQuery, SearchRequest, SearchResponse } from "@/types/api";

/** `GET /api/search?q=…` —— 非分页，用 `limit` 截断。 */
export function searchGet(params: SearchQuery): Promise<SearchResponse> {
  return request<SearchResponse>("GET", "/api/search", {
    query: {
      q: params.q,
      stack: params.stack,
      category: params.category,
      level: params.level,
      type: params.type,
      limit: params.limit,
      include_overview: params.include_overview,
    },
  });
}

/** `POST /api/search` —— body `{q, limit}`，命令面板用（`API.md` §3.7）。 */
export function searchPost(body: SearchRequest): Promise<SearchResponse> {
  return request<SearchResponse>("POST", "/api/search", { body });
}
