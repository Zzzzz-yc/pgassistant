/**
 * 资料域 API client（Owner：资料页 M2；`docs/contracts/MODULES.md` §3.2）。
 *
 * 只做「端点 → 类型」的映射，不缓存、不拼前缀、不处理错误：
 * 错误一律由 `lib/http.ts` 转成 `ApiError`，由调用方（hooks / 页面）决定展示。
 *
 * 两条跨模块约定：
 * - `slug` 含 `/`（D2），拼接时**不做 URL 编码**（`API.md` §1.9），否则后端路由匹配不到；
 * - `cover_path` / `media_base` 是 content 根相对路径，拼成 `/media/...` 的活由页面做（D24），
 *   本文件不猜任何前缀。
 */
import { request } from "@/lib/http";
import type {
  RelatedResponse,
  ResourceDetail,
  ResourceListQuery,
  ResourceListResponse,
  TaxonomyResponse,
} from "@/types/api";

/** `GET /api/taxonomy` —— 分类树（含 `stacks[].label`，M2 起非空）。 */
export function getTaxonomy(): Promise<TaxonomyResponse> {
  return request<TaxonomyResponse>("GET", "/api/taxonomy");
}

/**
 * `GET /api/resources` —— 分页列表。
 *
 * `undefined` / `""` 的查询参数会被 `http.ts` 丢掉，所以可以直接把筛选状态整包传进来。
 */
export function listResources(params: ResourceListQuery = {}): Promise<ResourceListResponse> {
  return request<ResourceListResponse>("GET", "/api/resources", {
    query: {
      stack: params.stack,
      category: params.category,
      level: params.level,
      type: params.type,
      source: params.source,
      q: params.q,
      page: params.page,
      size: params.size,
      sort: params.sort,
    },
  });
}

/** `GET /api/resources/{slug}` —— 详情（正文、`toc`、`prev/next`、`media_base`）。 */
export function getResource(slug: string): Promise<ResourceDetail> {
  return request<ResourceDetail>("GET", `/api/resources/${slug}`);
}

/** `GET /api/resources/{slug}/related` —— 同分类推荐，上限 6（§11）。 */
export function getRelated(slug: string, limit = 6): Promise<RelatedResponse> {
  return request<RelatedResponse>("GET", `/api/resources/${slug}/related`, {
    query: { limit },
  });
}
