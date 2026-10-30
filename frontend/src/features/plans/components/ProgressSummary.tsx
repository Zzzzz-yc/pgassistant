/**
 * ProgressSummary（Owner：计划模块 M3，§9.4 业务组件表）。
 *
 * 「进度环 + 三个数字（完成条目 / 已投入学时 / 剩余周数）」。
 * 数字一律 `tnum`（等宽数字，§9.3），百分比与周数由后端算好（§4.6），
 * 组件只负责展示，不在前端二次四舍五入（避免和列表页数字对不上）。
 */
import { useTranslation } from "react-i18next";

import { formatDuration } from "@/components/business/DurationLabel";
import { ProgressRing, cn, type RingSize } from "@/components/ui";
import type { PlanProgress } from "@/types/api";

export interface ProgressSummaryProps {
  progress: PlanProgress | null;
  size?: RingSize;
  /** 紧凑版：卡片里用，统计项横排且不换行。 */
  compact?: boolean;
  className?: string;
}

export function ProgressSummary({
  progress,
  size = "md",
  compact = false,
  className,
}: ProgressSummaryProps) {
  const { t } = useTranslation("plans");

  const percent = progress?.percent ?? 0;
  const done = progress?.done_items ?? 0;
  const total = (progress?.done_items ?? 0) + (progress?.todo_items ?? 0) + (progress?.skipped_items ?? 0);
  const invested = progress?.invested_minutes ?? 0;
  const remaining = progress?.remaining_weeks ?? 0;

  return (
    <div className={cn("plan-summary", compact && "plan-summary--compact", className)}>
      <ProgressRing percent={percent} size={size} />

      <dl className="plan-summary__stats">
        <div className="plan-summary__stat">
          <dt>{t("summary.done")}</dt>
          <dd className="tnum">{t("summary.items", { done, total })}</dd>
        </div>
        <div className="plan-summary__stat">
          <dt>{t("summary.invested")}</dt>
          <dd className="tnum">{formatDuration(invested)}</dd>
        </div>
        <div className="plan-summary__stat">
          <dt>{t("summary.remaining")}</dt>
          <dd className="tnum">{t("summary.weeks", { weeks: remaining })}</dd>
        </div>
      </dl>

      {progress && progress.skipped_items > 0 ? (
        <p className="plan-summary__skipped">
          {t("summary.skipped", { count: progress.skipped_items })}
        </p>
      ) : null}
    </div>
  );
}
