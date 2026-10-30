/**
 * Button（需求文档 §9.4「基础」：primary / secondary / ghost / danger × sm / md / lg）。
 *
 * 五态：
 * - hover：变体各自的背景/边框变化（`.ui-btn--*:hover`）
 * - focus-visible：2px primary 环 + offset 2px（§9.7）
 * - active：下移 1px + 更深的背景
 * - disabled：原生 `disabled`，opacity .45 + `cursor: not-allowed`
 * - loading：左侧 Spinner + `aria-busy="true"`，同时阻止重复提交（disabled 但保持满不透明）
 */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 提交中：显示 Spinner、忽略点击、`aria-busy`。 */
  loading?: boolean;
  /** loading 时给读屏的说明（默认沿用 Spinner 的「加载中」）。 */
  loadingLabel?: string;
  iconLeft?: ReactNode;
  iconRight?: ReactNode;
  /** 占满父容器宽度（表单底部的主按钮）。 */
  block?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    loadingLabel,
    iconLeft,
    iconRight,
    block = false,
    className,
    children,
    type = "button",
    disabled,
    onClick,
    ...rest
  },
  ref,
) {
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      className={cn(
        "ui-btn",
        `ui-btn--${variant}`,
        size !== "md" && `ui-btn--${size}`,
        block && "ui-btn--block",
        loading && "is-loading",
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      onClick={(event) => {
        if (loading) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
    >
      {loading ? <Spinner size="sm" label={loadingLabel} /> : iconLeft}
      <span className="ui-btn__label">{children}</span>
      {loading ? null : iconRight}
    </button>
  );
});
