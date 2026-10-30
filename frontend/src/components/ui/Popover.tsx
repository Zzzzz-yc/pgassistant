/**
 * Popover（需求文档 §9.4「容器与浮层」）。
 *
 * Esc 关闭、点击外部关闭、关闭后焦点回到 popover 内（Radix 自带）。
 *
 * 五态：Popover 容器不是可点控件——hover / focus-visible / active 由触发元素提供；
 * disabled / loading **不适用**（禁用状态由调用方把 trigger 换成 disabled 的按钮）。
 */
import * as PopoverPrimitive from "@radix-ui/react-popover";
import type { ReactNode } from "react";

export interface PopoverProps {
  trigger: ReactNode;
  children: ReactNode;
  align?: "start" | "center" | "end";
  side?: "top" | "right" | "bottom" | "left";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 面板的可访问名。 */
  ariaLabel?: string;
}

export function Popover({
  trigger,
  children,
  align = "start",
  side = "bottom",
  open,
  onOpenChange,
  ariaLabel,
}: PopoverProps) {
  return (
    <PopoverPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <PopoverPrimitive.Trigger asChild>{trigger}</PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          className="ui-popover__content"
          align={align}
          side={side}
          sideOffset={6}
          collisionPadding={8}
          aria-label={ariaLabel}
        >
          {children}
          <PopoverPrimitive.Arrow className="ui-popover__arrow" width={10} height={5} />
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
