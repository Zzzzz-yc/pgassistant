/**
 * 侧栏内看资料 —— `/plans/:planId/items/:slug(*)`（Owner：计划模块 M3，§9.1）。
 *
 * §9.1：「点击条目 → 右侧内容区加载该资料，**不跳页**（保持左侧上下文）」。
 * 实测差异：路由变了但 `AppShell` 与侧栏树不重建，所以展开状态与计划上下文天然保留；
 * 「返回计划」按钮回到 `/plans/:planId`，时间轴滚动位置由 PlanPage 从 sessionStorage 还原。
 *
 * 这个页面只做**阅读**：计划树的编辑在 PlanPage 上做，避免同一份数据两个入口写。
 */
import { ArrowLeft, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { DurationLabel } from "@/components/business/DurationLabel";
import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import { MarkdownView } from "@/components/business/MarkdownView";
import { Badge, Button, Skeleton } from "@/components/ui";
import { usePlan } from "@/features/plans/hooks";
import { usePlanResource } from "@/features/plans/usePlanResource";
import { ApiError } from "@/lib/http";

import "@/features/plans/plans.css";

export default function PlanItemPage() {
  const params = useParams();
  const planId = Number(params.planId);
  // v6 splat：路谱写 `plans/:planId/items/*`，其余段落在 `params["*"]`（D2：slug 含 `/`）。
  // 兼容早期 `:slug(*)` 写法，两种路由形态都能取到内容参数。
  const slug = (params["*"] ?? (params as Record<string, string | undefined>).slug ?? "").replace(
    /^\//,
    "",
  );
  const { t } = useTranslation(["plans", "common"]);
  const navigate = useNavigate();

  const plan = usePlan(Number.isInteger(planId) && planId > 0 ? planId : undefined);
  const resource = usePlanResource(slug || null);

  const item = plan.data?.stages
    .flatMap((stage) => stage.items.map((entry) => ({ item: entry, stage })))
    .find(({ item: entry }) => entry.resource_slug === slug);

  const backToPlan = () => navigate(`/plans/${planId}`);

  if (!slug) {
    return (
      <section className="page page-enter plan-reading">
        <EmptyState
          title={t("reading.missing")}
          action={
            <Button variant="primary" onClick={backToPlan}>
              {t("reading.back")}
            </Button>
          }
        />
      </section>
    );
  }

  return (
    <section className="page page-enter plan-reading">
      <header className="plan-reading__head">
        <Button
          variant="ghost"
          size="sm"
          iconLeft={<ArrowLeft size={14} aria-hidden="true" />}
          onClick={backToPlan}
        >
          {t("reading.back")}
        </Button>
        {item ? (
          <span className="plan-reading__context">
            <span className="plan-reading__stage">{item.stage.title}</span>
            <DurationLabel minutes={item.item.est_minutes} className="tnum" />
            {item.item.status === "done" ? (
              <Badge tone="primary">{t("summary.done")}</Badge>
            ) : null}
          </span>
        ) : null}
      </header>

      {resource.isLoading ? (
        <div className="plan-reading__body" aria-hidden="true">
          <Skeleton width="50%" height={26} />
          <Skeleton width="100%" height={16} />
          <Skeleton width="100%" height={16} />
          <Skeleton width="90%" height={16} />
          <Skeleton width="100%" height={180} />
        </div>
      ) : resource.isError ? (
        resource.error instanceof ApiError && resource.error.code === "RESOURCE_NOT_FOUND" ? (
          <EmptyState
            title={t("plan.item.unavailable")}
            description={t("reading.missing")}
            action={
              <Button variant="primary" onClick={backToPlan}>
                {t("reading.back")}
              </Button>
            }
          />
        ) : (
          <ErrorState
            code={resource.error instanceof ApiError ? resource.error.code : "INTERNAL"}
            detail={resource.error}
            onRetry={() => void resource.refetch()}
          />
        )
      ) : resource.data ? (
        <article className="plan-reading__body">
          <div className="plan-reading__meta">
            <h1 className="plan-reading__title">{resource.data.title}</h1>
            <Link className="plan-reading__full" to={`/library/${slug}`}>
              <ExternalLink size={13} aria-hidden="true" />
              {t("reading.openFull")}
            </Link>
          </div>
          <MarkdownView markdown={resource.data.body_md} toc={resource.data.toc} />
        </article>
      ) : null}

      {item?.item.note ? (
        <aside className="plan-reading__note">
          <strong>{t("plan.item.note")}</strong>
          <p>{item.item.note}</p>
        </aside>
      ) : null}
    </section>
  );
}
