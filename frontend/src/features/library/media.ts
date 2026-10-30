/**
 * 媒体 URL 解析（Owner：资料页 M2；`API.md` §1.10 / D24）。
 *
 * 全项目只有一种拼法：`/media/{content 根相对路径}`。
 *
 * - 封面：`ResourceSummary.cover_path` → `/media/{cover_path}`；`null` = 没有封面，
 *   **不要**拼 `/media/null`（组件会回退首字块）。`ResourceCard` 只接收已解析的 `coverUrl`，
 *   所以拼前缀的活只能在这里做。
 * - 正文图片：交给 `MarkdownView` 的 `mediaBase` prop（D24-24.2.6，组件库实现改写），
 *   页面**不预改写正文**——两份实现必然漂移。
 * - 编码：逐段 `encodeURIComponent`（保留 `/`），已经含 `%` 的段原样保留，不二次编码。
 * - 不写协议与主机：端口会在 8001–8010 之间变（§3.2），只能同源相对路径。
 */

/** content 根相对路径 → 可访问 URL；空值返回 `null`（不是 `/media/null`）。 */
export function mediaUrl(relativePath: string | null | undefined): string | null {
  if (!relativePath) return null;
  const trimmed = relativePath.replace(/^\/+/, "").trim();
  if (!trimmed) return null;
  const encoded = trimmed
    .split("/")
    .map((segment) => (segment.includes("%") ? segment : encodeURIComponent(segment)))
    .join("/");
  return `/media/${encoded}`;
}

/** 资料详情页地址（slug 含 `/`，按 §1.9 不做整串编码）。 */
export function resourceHref(slug: string): string {
  return `/library/${slug}`;
}

/** 资料库地址（筛选状态走 query，便于刷新/分享）。 */
export function libraryHref(query: Record<string, string | number | undefined> = {}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === "" || value === null) continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `/library?${qs}` : "/library";
}
