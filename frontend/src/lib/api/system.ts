/**
 * 系统域接口（Owner：前端骨架 M0）。
 * 只读探针，免令牌；助手模块与设置页都靠它判断降级态（§6.6）。
 */
import { request } from "@/lib/http";
import type { HealthResponse } from "@/types/api";

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("GET", "/api/health");
}
