/**
 * Tooltip（需求文档 §9.4「基础」+ §9.6「Offline：按钮禁用并 Tooltip 说明原因」）。
 *
 * 五态：Tooltip 自身不是可点控件——
 * - hover / focus-visible：由**触发元素**提供（这里把 delay 压到 200ms，触屏不弹）
 * - active / disabled / loading **不适用**（它不是按钮；被禁用的按钮要用
 *   `<span tabIndex={0}>` 包一层才能收到 hover，见 gallery 的 Offline 示例）
 */
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  /** 打开延迟（ms），默认 200。 */
  delayMs?: number;
}

export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={200} skipDelayDuration={300}>
      {children}
    </TooltipPrimitive.Provider>
  );
}

export function Tooltip({ content, children, side = "top", delayMs }: TooltipProps) {
  return (
    <TooltipPrimitive.Root delayDuration={delayMs}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content className="ui-tooltip__content" side={side} sideOffset={6} collisionPadding={8}>
          {content}
          <TooltipPrimitive.Arrow className="ui-tooltip__arrow" width={10} height={5} />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
