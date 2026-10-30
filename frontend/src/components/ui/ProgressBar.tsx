/**
 * ProgressBar（需求文档 §9.4「基础」+ §9.6「INDEX_BUILDING → 进度条」）。
 *
 * 五态：hover / focus-visible / active / disabled / loading **不适用**——进度条是只读指示。
 * `value === null` 表示不确定进度（来回跑的条），用于"还不知道总数"的索引进度。
 */
import * as ProgressPrimitive from "@radix-ui/react-progress";

import { cn } from "./cn";

export type ProgressTone = "primary" | "accent" | "danger";

export interface ProgressBarProps {
  /** 0–100；`null` = 不确定进度。 */
  value: number | null;
  /** 左侧标签（如「正在建立索引」）。 */
  label?: string;
  /** 右侧数值文案（默认 `xx%`；不确定进度时不显示）。 */
  valueLabel?: string;
  tone?: ProgressTone;
  /** 高度（px），默认 6。 */
  height?: number;
  className?: string;
}

export function ProgressBar({
  value,
  label,
  valueLabel,
  tone = "primary",
  height,
  className,
}: ProgressBarProps) {
  const indeterminate = value === null;
  const clamped = indeterminate ? 0 : Math.min(100, Math.max(0, value));

  return (
    <div
      className={cn(
        "ui-progress",
        tone !== "primary" && `ui-progress--${tone}`,
        indeterminate && "ui-progress--indeterminate",
        className,
      )}
    >
      {label || (!indeterminate && valueLabel !== "") ? (
        <div className="ui-progress__meta">
          <span>{label}</span>
          {indeterminate ? null : <span className="tnum">{valueLabel ?? `${Math.round(clamped)}%`}</span>}
        </div>
      ) : null}
      <ProgressPrimitive.Root
        className="ui-progress__track"
        value={indeterminate ? null : clamped}
        max={100}
        style={height ? { height } : undefined}
        aria-label={label ?? "进度"}
        aria-valuetext={indeterminate ? "进行中" : `${Math.round(clamped)}%`}
      >
        <ProgressPrimitive.Indicator
          className="ui-progress__fill"
          style={{ width: indeterminate ? "40%" : `${clamped}%` }}
        />
      </ProgressPrimitive.Root>
    </div>
  );
}
