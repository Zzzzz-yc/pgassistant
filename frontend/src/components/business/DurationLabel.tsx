/**
 * DurationLabel（业务组件，需求文档 §9.4 卡片"时长"）。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——纯文本标签，
 * 不可交互、无异步内容。
 *
 * 口径（§4.6 第 2 条）：`duration_min` 0–600，`null` 表示未知；数字用等宽数字（§9.3）。
 */
import { Clock } from "lucide-react";

export interface DurationLabelProps {
  /** 分钟数；`null` / `undefined` 显示「时长未知」。 */
  minutes: number | null | undefined;
  /** 未知时的文案。 */
  unknownLabel?: string;
  /** 传 `false` 隐藏时钟图标（密集排版时用）。 */
  withIcon?: boolean;
  className?: string;
}

/** 分钟 → 人话：`45` → `45 分钟`；`90` → `1 小时 30 分`；`120` → `2 小时`。 */
export function formatDuration(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  if (total < 60) return `${total} 分钟`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest === 0 ? `${hours} 小时` : `${hours} 小时 ${rest} 分`;
}

export function DurationLabel({
  minutes,
  unknownLabel = "时长未知",
  withIcon = true,
  className,
}: DurationLabelProps) {
  const known = typeof minutes === "number" && Number.isFinite(minutes);
  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        color: "var(--text-muted)",
        fontSize: 12,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {withIcon ? <Clock size={12} aria-hidden="true" /> : null}
      {known ? formatDuration(minutes as number) : unknownLabel}
    </span>
  );
}
