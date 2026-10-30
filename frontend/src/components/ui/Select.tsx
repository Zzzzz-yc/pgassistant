/**
 * Select（需求文档 §9.4「基础」，禁止原生 select 裸奔 → Radix Select 封装）。
 *
 * 五态：
 * - hover：边框变 primary
 * - focus-visible：2px focus 环（Radix 把焦点留在 trigger 上）
 * - active/open：底色 `--primary-soft`、箭头翻转
 * - disabled：`disabled` + opacity .45
 * - loading：trigger 内 Spinner 替代箭头，`aria-busy`，同时禁止展开
 */
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";
import type { FieldSize } from "./field";

export interface SelectOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

export interface SelectProps {
  /** 受控值；`undefined` 显示 placeholder。 */
  value?: string;
  onValueChange: (value: string) => void;
  options: readonly SelectOption[];
  placeholder?: string;
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  size?: FieldSize;
  disabled?: boolean;
  loading?: boolean;
  /** 无选中项时的读屏名（Radix 需要可访问名）。 */
  ariaLabel?: string;
  className?: string;
  fieldClassName?: string;
  id?: string;
}

export function Select({
  value,
  onValueChange,
  options,
  placeholder = "请选择",
  label,
  hint,
  error,
  size = "md",
  disabled = false,
  loading = false,
  ariaLabel,
  className,
  fieldClassName,
  id,
}: SelectProps) {
  const autoId = useId();
  const triggerId = id ?? autoId;
  const hintId = `${triggerId}-hint`;
  const errorId = `${triggerId}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");

  return (
    <div className={cn("ui-field", fieldClassName)}>
      {label ? (
        <label className="ui-field__label" htmlFor={triggerId}>
          {label}
        </label>
      ) : null}

      <SelectPrimitive.Root
        value={value}
        onValueChange={onValueChange}
        disabled={disabled || loading}
      >
        <SelectPrimitive.Trigger
          id={triggerId}
          className={cn("ui-select__trigger", size !== "md" && `ui-select__trigger--${size}`, className)}
          aria-label={label ? undefined : ariaLabel}
          aria-describedby={describedBy || undefined}
          aria-busy={loading || undefined}
          aria-invalid={error ? true : undefined}
        >
          <SelectPrimitive.Value placeholder={placeholder} className="ui-select__value" />
          {loading ? (
            <Spinner size="sm" />
          ) : (
            <SelectPrimitive.Icon asChild>
              <ChevronDown size={15} className="ui-select__chevron" aria-hidden="true" />
            </SelectPrimitive.Icon>
          )}
        </SelectPrimitive.Trigger>

        <SelectPrimitive.Portal>
          <SelectPrimitive.Content className="ui-select__content" position="popper" sideOffset={4}>
            <SelectPrimitive.Viewport className="ui-select__viewport">
              {options.map((option) => (
                <SelectPrimitive.Item
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                  className="ui-select__item"
                >
                  <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
                  <SelectPrimitive.ItemIndicator>
                    <Check size={14} className="ui-select__check" aria-hidden="true" />
                  </SelectPrimitive.ItemIndicator>
                </SelectPrimitive.Item>
              ))}
            </SelectPrimitive.Viewport>
          </SelectPrimitive.Content>
        </SelectPrimitive.Portal>
      </SelectPrimitive.Root>

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
}
