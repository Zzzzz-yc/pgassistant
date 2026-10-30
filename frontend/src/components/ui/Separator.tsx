/**
 * Separator（需求文档 §9.4「基础」）。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——1px 分隔线
 * 是纯装饰（Radix 会给它 `role="none"`；装饰性分隔线不进读屏的 tab 序列）。
 */
import * as SeparatorPrimitive from "@radix-ui/react-separator";

import { cn } from "./cn";

export interface SeparatorProps {
  orientation?: "horizontal" | "vertical";
  /** 装饰性（默认 true）：不加 `role="separator"`，读屏忽略。 */
  decorative?: boolean;
  className?: string;
}

export function Separator({ orientation = "horizontal", decorative = true, className }: SeparatorProps) {
  return (
    <SeparatorPrimitive.Root
      orientation={orientation}
      decorative={decorative}
      className={cn("ui-sep", `ui-sep--${orientation}`, className)}
    />
  );
}
