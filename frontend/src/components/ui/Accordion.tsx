/**
 * Accordion（需求文档 §9.4「容器与浮层」）。
 *
 * 键盘：Enter / Space 展开，↑ / ↓ 在条目间移动，Home / End 跳首尾（Radix 自带）。
 *
 * 五态（落在 trigger 上）：
 * - hover：文字变 primary + 底色 `--bg`
 * - focus-visible：2px 内嵌 focus 环
 * - active：底色 `--primary-soft`
 * - disabled：`disabled` + opacity .45
 * - loading：**不适用**（折叠是本地状态；异步内容用面板内 Skeleton）
 */
import * as AccordionPrimitive from "@radix-ui/react-accordion";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

export interface AccordionItem {
  value: string;
  title: ReactNode;
  content: ReactNode;
  disabled?: boolean;
}

export interface AccordionProps {
  items: readonly AccordionItem[];
  /** 允许同时展开多个（默认 false：一次一个）。 */
  multiple?: boolean;
  defaultOpen?: string[];
  className?: string;
}

function renderItems(items: readonly AccordionItem[]) {
  return items.map((item) => (
    <AccordionPrimitive.Item key={item.value} value={item.value} className="ui-accordion__item">
      <AccordionPrimitive.Header>
        <AccordionPrimitive.Trigger className="ui-accordion__trigger" disabled={item.disabled}>
          <span>{item.title}</span>
          <ChevronDown size={16} className="ui-accordion__chevron" aria-hidden="true" />
        </AccordionPrimitive.Trigger>
      </AccordionPrimitive.Header>
      <AccordionPrimitive.Content className="ui-accordion__content">
        {item.content}
      </AccordionPrimitive.Content>
    </AccordionPrimitive.Item>
  ));
}

export function Accordion({ items, multiple = false, defaultOpen, className }: AccordionProps) {
  const rootClass = className ? `ui-accordion ${className}` : "ui-accordion";

  // 单选/多选是两套 props 类型，分支写清楚，不用联合类型硬塞
  if (multiple) {
    return (
      <AccordionPrimitive.Root type="multiple" defaultValue={defaultOpen} className={rootClass}>
        {renderItems(items)}
      </AccordionPrimitive.Root>
    );
  }

  return (
    <AccordionPrimitive.Root
      type="single"
      collapsible
      defaultValue={defaultOpen?.[0]}
      className={rootClass}
    >
      {renderItems(items)}
    </AccordionPrimitive.Root>
  );
}
