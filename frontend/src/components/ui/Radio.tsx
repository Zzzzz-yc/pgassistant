/**
 * Radio / RadioGroup（需求文档 §9.4「基础」，禁止原生 radio 裸奔 → Radix 封装）。
 *
 * 五态（落在圆点上）：
 * - hover：边框变 primary
 * - focus-visible：2px focus 环
 * - active：底色 `--primary-soft`
 * - disabled：`disabled` + opacity .45（整组或单项）
 * - loading：标签前 Spinner（选项数据正在拉取），同时只读
 *
 * 无障碍：Radix RadioGroup 自带 `role="radiogroup"` 与 roving tabindex（↑↓/←→ 切换）。
 */
import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { useId, type ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export interface RadioOption {
  value: string;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
}

export interface RadioProps {
  value: string;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
  id?: string;
}

/** 单个选项（一般由 `RadioGroup` 内部渲染；需要单独排版时才直接用）。 */
export function Radio({ value, label, hint, disabled, id }: RadioProps) {
  const autoId = useId();
  const itemId = id ?? `${autoId}-${value}`;

  return (
    <div className="ui-radio">
      <RadioGroupPrimitive.Item
        id={itemId}
        value={value}
        className="ui-radio__dot"
        disabled={disabled}
      />
      <label htmlFor={itemId} style={{ cursor: disabled ? "not-allowed" : "pointer" }}>
        <span>{label}</span>
        {hint ? <span className="ui-field__hint" style={{ display: "block" }}>{hint}</span> : null}
      </label>
    </div>
  );
}

export interface RadioGroupProps {
  value?: string;
  onValueChange: (value: string) => void;
  options: readonly RadioOption[];
  /** 组的可访问名（传了 `label` 时可以省略）。 */
  ariaLabel?: string;
  label?: ReactNode;
  orientation?: "vertical" | "horizontal";
  disabled?: boolean;
  loading?: boolean;
  className?: string;
}

export function RadioGroup({
  value,
  onValueChange,
  options,
  ariaLabel,
  label,
  orientation = "vertical",
  disabled = false,
  loading = false,
  className,
}: RadioGroupProps) {
  return (
    <fieldset className={cn("ui-radio-group", className)} style={{ border: 0, padding: 0, margin: 0 }}>
      {label ? <legend className="ui-field__label">{label}</legend> : null}
      <RadioGroupPrimitive.Root
        value={value}
        onValueChange={onValueChange}
        disabled={disabled || loading}
        aria-label={ariaLabel}
        orientation={orientation}
        style={{
          display: "flex",
          flexDirection: orientation === "vertical" ? "column" : "row",
          gap: "var(--space-2)",
        }}
      >
        {options.map((option) => (
          <div className="ui-radio" key={option.value}>
            <RadioGroupPrimitive.Item
              value={option.value}
              id={`${option.value}-radio`}
              className="ui-radio__dot"
              disabled={option.disabled}
            />
            <label
              htmlFor={`${option.value}-radio`}
              style={{ cursor: option.disabled ? "not-allowed" : "pointer" }}
            >
              <span>{option.label}</span>
              {option.hint ? (
                <span className="ui-field__hint" style={{ display: "block" }}>
                  {option.hint}
                </span>
              ) : null}
            </label>
          </div>
        ))}
      </RadioGroupPrimitive.Root>
      {loading ? (
        <span className="ui-field__hint" style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-1)" }}>
          <Spinner size="sm" />
          选项加载中…
        </span>
      ) : null}
    </fieldset>
  );
}
