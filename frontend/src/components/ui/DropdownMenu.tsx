/**
 * DropdownMenu（需求文档 §9.4「容器与浮层」；§9.1「计划切换放在侧栏的计划名下拉里」）。
 *
 * 键盘：↑ / ↓ 移动、Enter 选中、Esc 关闭、Home / End 跳首尾（Radix 自带）；
 * 关闭后焦点回到 trigger（Radix 的 focus-return）。
 *
 * 五态（落在菜单项上）：
 * - hover / 高亮（`[data-highlighted]`）：`--primary-soft` 底 + primary 字
 * - focus-visible：2px 内嵌 focus 环
 * - active：`--primary-soft-hover` 底
 * - disabled：`[data-disabled]` + opacity .45
 * - loading：菜单项右侧 Spinner（"正在执行上一条命令"），该项同时禁用
 */
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { Spinner } from "./Spinner";

export type MenuEntry =
  | {
      kind: "item";
      key: string;
      label: ReactNode;
      icon?: ReactNode;
      onSelect?: () => void;
      danger?: boolean;
      disabled?: boolean;
      loading?: boolean;
    }
  | {
      kind: "checkbox";
      key: string;
      label: ReactNode;
      checked: boolean;
      onCheckedChange: (checked: boolean) => void;
      disabled?: boolean;
    }
  | { kind: "label"; key: string; label: ReactNode }
  | { kind: "separator"; key: string };

export interface DropdownMenuProps {
  /** 触发元素（必须能接收 ref：用元素而不是纯文本）。 */
  trigger: ReactNode;
  entries: readonly MenuEntry[];
  align?: "start" | "center" | "end";
  side?: "top" | "right" | "bottom" | "left";
  /** 受控开合（不传则自管）。 */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 菜单的可访问名。 */
  ariaLabel?: string;
}

export function DropdownMenu({
  trigger,
  entries,
  align = "start",
  side = "bottom",
  open,
  onOpenChange,
  ariaLabel,
}: DropdownMenuProps) {
  return (
    <DropdownMenuPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DropdownMenuPrimitive.Trigger asChild>{trigger}</DropdownMenuPrimitive.Trigger>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          className="ui-menu__content"
          align={align}
          side={side}
          sideOffset={6}
          aria-label={ariaLabel}
        >
          {entries.map((entry) => {
            if (entry.kind === "separator") {
              return <DropdownMenuPrimitive.Separator key={entry.key} className="ui-menu__separator" />;
            }
            if (entry.kind === "label") {
              return (
                <DropdownMenuPrimitive.Label key={entry.key} className="ui-menu__label">
                  {entry.label}
                </DropdownMenuPrimitive.Label>
              );
            }
            if (entry.kind === "checkbox") {
              return (
                <DropdownMenuPrimitive.CheckboxItem
                  key={entry.key}
                  className="ui-menu__item"
                  checked={entry.checked}
                  onCheckedChange={entry.onCheckedChange}
                  disabled={entry.disabled}
                >
                  {entry.label}
                  <DropdownMenuPrimitive.ItemIndicator>
                    <Check size={14} className="ui-menu__check" aria-hidden="true" />
                  </DropdownMenuPrimitive.ItemIndicator>
                </DropdownMenuPrimitive.CheckboxItem>
              );
            }
            return (
              <DropdownMenuPrimitive.Item
                key={entry.key}
                className={
                  entry.danger ? "ui-menu__item ui-menu__item--danger" : "ui-menu__item"
                }
                disabled={entry.disabled || entry.loading}
                onSelect={() => entry.onSelect?.()}
              >
                {entry.icon ? <span aria-hidden="true" className="ui-inline-icon">{entry.icon}</span> : null}
                <span>{entry.label}</span>
                {entry.loading ? (
                  <span style={{ marginLeft: "auto" }}>
                    <Spinner size="sm" />
                  </span>
                ) : null}
              </DropdownMenuPrimitive.Item>
            );
          })}
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  );
}
