/**
 * Tag（需求文档 §9.4「基础」：可选中筛选标签）。
 *
 * 五态：
 * - hover：边框变 primary（暗示可点）
 * - focus-visible：2px focus 环
 * - active：底色 → `--primary-soft`
 * - disabled：`disabled` + opacity .45
 * - loading：前置 Spinner（用于"筛选结果正在拉取"的乐观筛选标签）
 *
 * 无障碍：可选中时渲染 `<button aria-pressed>`；`onRemove` 时追加一个带 `aria-label`
 * 的小叉（不嵌套 button，避免 HTML 非法——移除键与主体是并列的两个 button）。
 */
import type { ReactNode } from "react";
import { X } from "lucide-react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export interface TagProps {
  children: ReactNode;
  /** 选中态（渲染成 `aria-pressed="true"`）。 */
  selected?: boolean;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
  size?: "sm" | "md";
  /** 传了就显示移除键；`removeLabel` 是它的 aria-label。 */
  onRemove?: () => void;
  removeLabel?: string;
  className?: string;
  title?: string;
}

export function Tag({
  children,
  selected,
  onClick,
  disabled = false,
  loading = false,
  size = "sm",
  onRemove,
  removeLabel,
  className,
  title,
}: TagProps) {
  const interactive = typeof onClick === "function";
  const classes = cn(
    "ui-tag",
    size !== "sm" && `ui-tag--${size}`,
    selected && "is-selected",
    className,
  );

  if (!interactive && !onRemove) {
    return (
      <span className={classes} title={title}>
        {loading ? <Spinner size="sm" /> : null}
        {children}
      </span>
    );
  }

  return (
    <span className="ui-inline-icon" style={{ display: "inline-flex", gap: "var(--space-1)" }}>
      {interactive ? (
        <button
          type="button"
          className={classes}
          aria-pressed={selected ?? false}
          disabled={disabled || loading}
          onClick={onClick}
          title={title}
        >
          {loading ? <Spinner size="sm" /> : null}
          {children}
        </button>
      ) : (
        <span className={classes} title={title}>
          {loading ? <Spinner size="sm" /> : null}
          {children}
        </span>
      )}
      {onRemove ? (
        <button
          type="button"
          className="ui-tag__remove"
          aria-label={removeLabel ?? `移除 ${typeof children === "string" ? children : "标签"}`}
          disabled={disabled}
          onClick={onRemove}
        >
          <X size={11} aria-hidden="true" />
        </button>
      ) : null}
    </span>
  );
}
