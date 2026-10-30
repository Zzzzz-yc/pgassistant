/**
 * 索引与私有资料接口（Owner：打磨模块 M5，`docs/contracts/MODULES.md` §3.2）。
 *
 * 三条与后端实现对齐的注意点（写错会静默失效，不是 4xx）：
 * 1. `IndexRunState` 的封闭集合是 `idle | running | failed`，**没有 `done`**：
 *    成功终态是 `state="idle"` 且 `percent=100`（`schemas/models.py`）；
 * 2. `rebuild` 的 202 已经带回 `status`，所以点完按钮可以直接进轮询，
 *    不必先等一次 `GET /api/index/status`；
 * 3. `rescan` 的 body 可省略，这里**不发 body**（`PrivateRescanRequest` 是空对象，
 *    带了多余字段会被 `extra="forbid"` 判 422）。
 */
import { request } from "@/lib/http";
import type {
  IndexRebuildRequest,
  IndexRebuildResponse,
  IndexStatus,
  PrivateRescanResponse,
} from "@/types/api";

/** POST /api/index/rebuild —— 202 + `task_id`；已有任务 409 `INDEX_BUILDING`，限流 429。 */
export function rebuildIndex(body: IndexRebuildRequest = {}): Promise<IndexRebuildResponse> {
  return request<IndexRebuildResponse>("POST", "/api/index/rebuild", { body });
}

/** GET /api/index/status —— 省略 `task_id` 返回最近一次任务。 */
export function getIndexStatus(taskId?: string | null): Promise<IndexStatus> {
  return request<IndexStatus>("GET", "/api/index/status", {
    query: { task_id: taskId ?? undefined },
  });
}

/** POST /api/private/rescan —— 重扫私有目录（增量 diff，手动触发）。 */
export function rescanPrivate(): Promise<PrivateRescanResponse> {
  return request<PrivateRescanResponse>("POST", "/api/private/rescan");
}
