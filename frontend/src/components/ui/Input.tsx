/**
 * Input（需求文档 §9.4「基础」）。
 *
 * 五态：
 * - hover：边框向 primary 靠（`color-mix` 55%）
 * - focus-visible：边框变 primary + 2px focus 环（§9.7）
 * - active：按下时底色转 4% primary 混合、边框加深（轻反馈；文本框按下即聚焦，
 *   所以这一态一闪而过，但不能没有）
 * - disabled：原生 `disabled`，底色退成 `--bg`
 * - loading：右侧 Spinner（异步校验/远程建议场景），并用 `aria-busy` 告知读屏
 *
 * 无障碍：传 `label` 时自动用 `useId` 把 `<label for>` 与输入框关联；错误信息走
 * `aria-invalid` + `aria-describedby`。不传 `label` 时调用方必须给 `aria-label`。
 */
import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";
import type { FieldSize } from "./field";

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  label?: ReactNode;
  /** 辅助说明（永远可见）。 */
  hint?: ReactNode;
  /** 错误文案；有值时边框变 danger 且 `aria-invalid`。 */
  error?: ReactNode;
  size?: FieldSize;
  loading?: boolean;
  iconLeft?: ReactNode;
  iconRight?: ReactNode;
  /** 外层 Field 的 className（label + 输入 + 提示的整体）。 */
  fieldClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    label,
    hint,
    error,
    size = "md",
    loading = false,
    iconLeft,
    iconRight,
    fieldClassName,
    className,
    id,
    required,
    disabled,
    ...rest
  },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");

  return (
    <div className={cn("ui-field", fieldClassName)}>
      {label ? (
        <label className="ui-field__label" htmlFor={inputId}>
          {label}
          {required ? (
            <span className="ui-field__required" aria-hidden="true">
              *
            </span>
          ) : null}
        </label>
      ) : null}

      <div className="ui-input-wrap">
        {iconLeft ? (
          <span className="ui-input__icon" aria-hidden="true">
            {iconLeft}
          </span>
        ) : null}
        <input
          {...rest}
          ref={ref}
          id={inputId}
          required={required}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          aria-busy={loading || undefined}
          className={cn(
            "ui-input",
            size !== "md" && `ui-input--${size}`,
            iconLeft && "ui-input--with-icon",
            className,
          )}
        />
        {loading || iconRight ? (
          <span className="ui-input__trailing">
            {loading ? <Spinner size="sm" /> : iconRight}
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="ui-field__error" id={errorId} role="alert">
          {error}
        </div>
      ) : null}
      {hint ? (
        <div className="ui-field__hint" id={hintId}>
          {hint}
        </div>
      ) : null}
    </div>
  );
});
