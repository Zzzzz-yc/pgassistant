/**
 * ProgressRing（需求文档 §9.4「基础」+ §9.5「计划详情顶部进度环」）。
 *
 * 五态：hover / focus-visible / active / disabled / loading **不适用**——只读指示。
 * 无障碍：`role="progressbar"` + `aria-valuenow/valuetext`，中间的数字是给眼睛的，
 * 读屏念的是 `aria-valuetext`（默认「完成 32%」）。
 */
import { cn } from "./cn";

export type RingSize = "sm" | "md" | "lg";

const RING_SIZE_PX: Record<RingSize, number> = { sm: 36, md: 52, lg: 72 };
const RING_STROKE: Record<RingSize, number> = { sm: 3, md: 4, lg: 5 };

export interface ProgressRingProps {
  /** 0–100 浮点（§4.6 的 percent）。 */
  percent: number;
  size?: RingSize;
  /** 环内文案；传 `null` 不显示。默认四舍五入的百分数。 */
  label?: string | null;
  tone?: "primary" | "accent";
  /** 读屏文案，默认「完成 xx%」。 */
  ariaLabel?: string;
  className?: string;
}

export function ProgressRing({
  percent,
  size = "md",
  label,
  tone = "primary",
  ariaLabel,
  className,
}: ProgressRingProps) {
  const clamped = Math.min(100, Math.max(0, percent));
  const px = RING_SIZE_PX[size];
  const stroke = RING_STROKE[size];
  const radius = (px - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);
  const text = label === undefined ? `${Math.round(clamped)}%` : label;

  return (
    <span
      className={cn("ui-ring", tone === "accent" && "ui-ring--accent", className)}
      style={{ width: px, height: px }}
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuetext={ariaLabel ?? `完成 ${Math.round(clamped)}%`}
    >
      <svg className="ui-ring__svg" width={px} height={px} aria-hidden="true">
        <circle
          className="ui-ring__track"
          cx={px / 2}
          cy={px / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
        />
        <circle
          className="ui-ring__value"
          cx={px / 2}
          cy={px / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      {text ? (
        <span className="ui-ring__label" aria-hidden="true">
          {text}
        </span>
      ) : null}
    </span>
  );
}
