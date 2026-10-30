/**
 * CommandPalette 挂载点（Owner：前端骨架 M0；M2 组件库接入真实面板）。
 *
 * 这个文件只做**一件事**：把布局层传下来的 `open` / `onClose` 交给组件库的
 * `components/business/CommandPalette`。
 *
 * 为什么保留这一层而不是让 AppShell 直接引业务组件：
 * - Ctrl/⌘+K 的绑定在 `AppShell`（`useHotkeys`），布局层是唯一的打开入口；
 * - 保持 M0 的装配点不变，后续要换成"资料页自己的搜索"时只改这一处或换 props。
 *
 * 焦点锁定 / Esc 关闭 / 关闭后焦点回到触发元素由 `ui/Dialog`（Radix Dialog）提供，
 * 见 `components/ui/Dialog.tsx` 的文件头。
 */
import { CommandPalette as BusinessCommandPalette } from "@/components/business/CommandPalette";

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  return <BusinessCommandPalette open={open} onClose={onClose} />;
}
