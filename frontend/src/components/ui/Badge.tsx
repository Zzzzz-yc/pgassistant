/**
 * Badge（需求文档 §9.4「基础」）。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——Badge 是
 * 纯展示的静态徽章（难度、类型、私有角标），不接收指针事件、不可聚焦、没有异步内容。
 * 需要"可点/可筛"的语义请用 `Tag`（带 `aria-pressed`）；需要"进行中"请用 `Spinner`。
 */
import type { ReactNode } from "react";

import { cn } from "./cn";

export type BadgeTone = "neutral" | "primary" | "accent" | "danger";
export type BadgeSize = "sm" | "md";

export interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone;
  size?: BadgeSize;
  /** 徽章前的图标（如难度符号）。 */
  icon?: ReactNode;
  className?: string;
  title?: string;
}

export function Badge({ children, tone = "neutral", size = "sm", icon, className, title }: BadgeProps) {
  return (
    <span
      className={cn("ui-badge", tone !== "neutral" && `ui-badge--${tone}`, size !== "sm" && `ui-badge--${size}`, className)}
      title={title}
    >
      {icon ? <span aria-hidden="true" className="ui-inline-icon">{icon}</span> : null}
      {children}
    </span>
  );
}
