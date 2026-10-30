/**
 * Skeleton（需求文档 §9.4「基础」+ §9.6「Loading：骨架屏与真实布局同尺寸」）。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——Skeleton 本身
 * 就是加载态的占位块，不可交互、不可聚焦。shimmer 是 §9.2 第 4 条允许的三种动效之一，
 * `prefers-reduced-motion` 下由 global.css 统一关掉。
 */
import { cn } from "./cn";

export type SkeletonShape = "block" | "text" | "title" | "circle";

export interface SkeletonProps {
  shape?: SkeletonShape;
  /** 显式尺寸（默认由 shape 决定）：传数字按 px。 */
  width?: number | string;
  height?: number | string;
  className?: string;
}

export function Skeleton({ shape = "block", width, height, className }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      className={cn("ui-skeleton", shape !== "block" && `ui-skeleton--${shape}`, className)}
      style={{ width, height }}
    />
  );
}
