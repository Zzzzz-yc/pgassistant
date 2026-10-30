/**
 * Textarea（需求文档 §9.4「基础」）。
 *
 * 五态：hover（边框向 primary）/ focus-visible（focus 环）/ active（按下时底色转 4%
 * primary 混合 + 边框加深；按下即聚焦，所以一闪而过）/ disabled（底色退化）/
 * loading（右侧 Spinner + `aria-busy`）。
 * 无障碍：`label` 自动 `for` 关联，错误走 `aria-invalid` + `aria-describedby`。
 */
import { forwardRef, useId, type ReactNode, type TextareaHTMLAttributes } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  loading?: boolean;
  /** 字数统计（受控时可传 `value.length/maxLength`）。 */
  counter?: ReactNode;
  fieldClassName?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, hint, error, loading = false, counter, fieldClassName, className, id, required, disabled, ...rest },
  ref,
) {
  const autoId = useId();
  const areaId = id ?? autoId;
  const hintId = `${areaId}-hint`;
  const errorId = `${areaId}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");

  return (
    <div className={cn("ui-field", fieldClassName)}>
      {label ? (
        <label className="ui-field__label" htmlFor={areaId}>
          {label}
          {required ? (
            <span className="ui-field__required" aria-hidden="true">
              *
            </span>
          ) : null}
        </label>
      ) : null}

      <div className="ui-input-wrap">
        <textarea
          {...rest}
          ref={ref}
          id={areaId}
          required={required}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          aria-busy={loading || undefined}
          className={cn("ui-textarea", className)}
        />
        {loading ? (
          <span className="ui-input__trailing">
            <Spinner size="sm" />
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
      {counter ? <div className="ui-field__hint tnum">{counter}</div> : null}
    </div>
  );
});
