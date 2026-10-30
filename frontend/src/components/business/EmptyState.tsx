/**
 * EmptyState（业务组件，需求文档 §9.4「EmptyState：自绘 SVG 插画 + 一句说明 + 一个主行动按钮」）。
 *
 * 插画是**本组件内联的 SVG**（不引第三方图、不走网络），用 `currentColor` 与
 * `var(--…)` 上色，尺寸固定 132×96（compact 84×60），换主题跟着 tokens 走。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——插画与文案是
 * 静态内容；唯一的交互是 `action`（由调用方传入一个 Button，五态在按钮上）。
 */
import type { ReactNode } from "react";

import { cn } from "@/components/ui";

export type EmptyIllustration = "library" | "search" | "plans";

export interface EmptyStateProps {
  /** 一句说明（"这里还什么都没有"这类）。 */
  title?: string;
  /** 补充解释（1–2 行）。 */
  description?: ReactNode;
  /** 主行动按钮（一个就够，§9.4）。 */
  action?: ReactNode;
  illustration?: EmptyIllustration;
  compact?: boolean;
  className?: string;
}

function Illustration({ variant }: { variant: EmptyIllustration }) {
  return (
    <svg
      className="empty-state__art"
      viewBox="0 0 132 96"
      fill="none"
      role="img"
      aria-hidden="true"
      focusable="false"
    >
      {/* 桌面 + 纸面 */}
      <rect
        x="16"
        y="22"
        width="66"
        height="52"
        rx="4"
        stroke="currentColor"
        strokeOpacity="0.45"
        strokeWidth="1.5"
        fill="var(--primary-soft)"
      />
      <path d="M26 34h30M26 42h46M26 50h46M26 58h22" stroke="currentColor" strokeOpacity="0.42" strokeWidth="1.5" strokeLinecap="round" />
      {/* 书脊阴影，给"一叠资料"的体积感 */}
      <rect
        x="24"
        y="12"
        width="66"
        height="52"
        rx="4"
        stroke="currentColor"
        strokeOpacity="0.28"
        strokeWidth="1.5"
        fill="var(--surface)"
      />

      {variant === "search" ? (
        <>
          <circle cx="86" cy="48" r="16" stroke="currentColor" strokeWidth="2" fill="var(--surface)" />
          <path d="M97 60l10 10" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        </>
      ) : null}

      {variant === "plans" ? (
        <>
          <path d="M76 24h34M76 34h24M76 44h30" stroke="currentColor" strokeOpacity="0.5" strokeWidth="1.5" strokeLinecap="round" />
          <circle cx="70" cy="24" r="3" fill="var(--accent)" />
          <circle cx="70" cy="34" r="3" fill="currentColor" fillOpacity="0.28" />
          <circle cx="70" cy="44" r="3" fill="currentColor" fillOpacity="0.28" />
        </>
      ) : null}

      {variant === "library" ? (
        <>
          <path d="M86 26h26v40H86z" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.5" fill="var(--surface)" />
          <path d="M92 36h14M92 44h14M92 52h8" stroke="currentColor" strokeOpacity="0.4" strokeWidth="1.5" strokeLinecap="round" />
        </>
      ) : null}
    </svg>
  );
}

export function EmptyState({
  title = "这里还什么都没有",
  description,
  action,
  illustration = "library",
  compact = false,
  className,
}: EmptyStateProps) {
  return (
    <div className={cn("empty-state", compact && "empty-state--compact", className)}>
      <Illustration variant={illustration} />
      <p className="empty-state__title">{title}</p>
      {description ? <p className="empty-state__desc">{description}</p> : null}
      {action ? <div className="empty-state__action">{action}</div> : null}
    </div>
  );
}
