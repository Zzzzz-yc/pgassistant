/**
 * `cn`：className 合并的唯一入口（组件内部用）。
 * 只做条件拼接，不做样式合并算法——样式冲突由 CSS 选择器顺序决定，避免引 tailwind-merge。
 */
import clsx, { type ClassValue } from "clsx";

export function cn(...values: ClassValue[]): string {
  return clsx(...values);
}
