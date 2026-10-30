/**
 * Tabs（需求文档 §9.4「容器与浮层」+ §9.7「Tabs 键盘可达」）。
 *
 * 键盘：← / → 切换、Home / End 跳首尾（Radix 的 roving tabindex 自带，激活即跟随）。
 *
 * 五态（落在 trigger 上）：
 * - hover：底色 `--bg` + 文字变正文色
 * - focus-visible：2px 内嵌 focus 环（避免被下边框裁掉）
 * - active：底色 `--primary-soft`
 * - disabled：`disabled` + opacity .45
 * - loading：**不适用**（Tab 切换是本地状态；内容加载由面板内 Skeleton 表达）
 */
import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ReactNode } from "react";

export interface TabItem {
  value: string;
  label: ReactNode;
  content: ReactNode;
  disabled?: boolean;
}

export interface TabsProps {
  items: readonly TabItem[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** Tab 列表的可访问名。 */
  ariaLabel?: string;
  className?: string;
}

export function Tabs({ items, value, defaultValue, onValueChange, ariaLabel, className }: TabsProps) {
  return (
    <TabsPrimitive.Root
      className={className}
      value={value}
      defaultValue={defaultValue ?? items[0]?.value}
      onValueChange={onValueChange}
    >
      <TabsPrimitive.List className="ui-tabs__list" aria-label={ariaLabel}>
        {items.map((item) => (
          <TabsPrimitive.Trigger
            key={item.value}
            value={item.value}
            className="ui-tabs__trigger"
            disabled={item.disabled}
          >
            {item.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>

      {items.map((item) => (
        <TabsPrimitive.Content key={item.value} value={item.value} className="ui-tabs__content">
          {item.content}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}
