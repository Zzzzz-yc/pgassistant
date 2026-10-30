/**
 * 设置域接口（Owner：打磨模块 M5，`docs/contracts/MODULES.md` §3.2）。
 *
 * 四个端点、三条硬约定：
 * 1. `GET` 的 `api_key` **只有掩码**（`sk-****1234`）——前端永远拿不到明文，
 *    所以输入框只写不读：`value` 恒为空串，掩码放 `placeholder`；
 * 2. `PUT` 是**局部更新**：只发用户真正改过的组；`api_key` 不传 = 保持原值；
 * 3. `POST /api/settings/test` 的探针失败也是 **200**（业务结果不是 HTTP 错误），
 *    所以这里不 `catch`，`ok=false` 由页面按 `message` / `error_code` 展示。
 */
import { request } from "@/lib/http";
import type {
  SettingsRead,
  SettingsTestRequest,
  SettingsTestResponse,
  SettingsWrite,
} from "@/types/api";

/** GET /api/settings —— 读取设置（密钥只出掩码）。 */
export function getSettings(): Promise<SettingsRead> {
  return request<SettingsRead>("GET", "/api/settings");
}

/** PUT /api/settings —— 局部更新；未提供的组保持原值。 */
export function putSettings(body: SettingsWrite): Promise<SettingsRead> {
  return request<SettingsRead>("PUT", "/api/settings", { body });
}

/** POST /api/settings/test —— 连通性探针；`override` 带配置时用请求里的值测、不落库。 */
export function testSettings(body: SettingsTestRequest): Promise<SettingsTestResponse> {
  return request<SettingsTestResponse>("POST", "/api/settings/test", { body });
}
