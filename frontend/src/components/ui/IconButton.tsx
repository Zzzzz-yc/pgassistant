/**
 * IconButton（需求文档 §9.4「基础」）。
 *
 * 五态与 Button 完全一致（同一套 `.ui-btn--*` 骨架 + 方形尺寸修饰 `.ui-icon-btn`）。
 * 无障碍：`label` 是**必填**的 `aria-label`（§9.7「图标按钮必须有 aria-label」），
 * 图标本身 `aria-hidden`，不给读屏念图形名。
 */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";
import type { ButtonSize, ButtonVariant } from "./Button";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> {
  /** 必填：读屏能听到的按钮名（例如「关闭」「翻到下一页」）。 */
  label: string;
  icon: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, variant = "ghost", size = "md", loading = false, className, type = "button", disabled, onClick, ...rest },
  ref,
) {
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      className={cn(
        "ui-btn",
        "ui-icon-btn",
        `ui-btn--${variant}`,
        size !== "md" && `ui-btn--${size}`,
        loading && "is-loading",
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      aria-label={label}
      title={label}
      onClick={(event) => {
        if (loading) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
    >
      {loading ? <Spinner size="sm" label={label} /> : <span aria-hidden="true" className="ui-inline-icon">{icon}</span>}
    </button>
  );
});
