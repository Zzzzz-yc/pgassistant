/**
 * Slider（需求文档 §9.4「基础」→ Radix Slider 封装，禁止原生 range 裸奔）。
 *
 * 五态（落在滑块上）：
 * - hover：thumbs 外扩 4px 的 `--primary-soft` 光晕
 * - focus-visible：2px focus 环
 * - active：拖动时光晕扩大到 6px + `cursor: grabbing`
 * - disabled：`data-disabled` + opacity .45
 * - loading：**不适用**（滑块是本地即时值；异步确认由父级禁用 + Spinner 表达）
 */
import * as SliderPrimitive from "@radix-ui/react-slider";
import { type ReactNode } from "react";

import { cn } from "./cn";

export interface SliderProps {
  /** 单值。多值用 `values`。 */
  value?: number;
  values?: number[];
  onValueChange: (values: number[]) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  label?: ReactNode;
  /** 右侧数值文案（默认显示第一个值）。 */
  valueLabel?: ReactNode;
  ariaLabel?: string;
  className?: string;
}

export function Slider({
  value,
  values,
  onValueChange,
  min = 0,
  max = 100,
  step = 1,
  disabled = false,
  label,
  valueLabel,
  ariaLabel,
  className,
}: SliderProps) {
  const current = values ?? (typeof value === "number" ? [value] : [min]);
  const shown = valueLabel ?? current.join(" – ");

  return (
    <div className={cn("ui-field", className)}>
      {label || valueLabel !== null ? (
        <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-2)" }}>
          {label ? <span className="ui-field__label">{label}</span> : <span />}
          <span className="tnum" style={{ color: "var(--text-muted)", fontSize: 12 }}>
            {shown}
          </span>
        </div>
      ) : null}

      <SliderPrimitive.Root
        className="ui-slider"
        value={current}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onValueChange={onValueChange}
        aria-label={label ? undefined : (ariaLabel ?? "数值")}
      >
        <SliderPrimitive.Track className="ui-slider__track">
          <SliderPrimitive.Range className="ui-slider__range" />
        </SliderPrimitive.Track>
        {current.map((_, index) => (
          <SliderPrimitive.Thumb
            key={index}
            className="ui-slider__thumb"
            aria-label={`${ariaLabel ?? "数值"} ${index + 1}`}
          />
        ))}
      </SliderPrimitive.Root>
    </div>
  );
}
