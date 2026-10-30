/**
 * SidebarPlanTree（Owner：计划模块 M3，§9.1「左侧固定侧栏就是当前激活计划的节点树」）。
 *
 * 由 AppShell 通过 `Sidebar` 的 `planTree` 插槽注入：**有 active 计划时**才渲染，
 * 没有时 `AppShell` 传 `null`，侧栏自动回退到章节树（§9.1 最后一条）。
 *
 * 结构：计划名 + 进度条 + 阶段（可展开）→ 条目（三态图标 + 绑定状态）。
 * 点条目 → `/plans/:planId/items/:slug`，**右侧内容区换内容、左侧树不重建**，
 * 所以展开状态天然保留（这正是 §9.1 相对"跳页"的收益）。
 */
import { CheckCircle2, ChevronDown, ChevronRight, Circle, MinusCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useMatch } from "react-router-dom";

import { ProgressBar } from "@/components/ui";
import type { ItemRead, PlanRead } from "@/types/api";

export interface SidebarPlanTreeProps {
  plan: PlanRead;
  /** 移动端点完链接后关抽屉（由 Sidebar 透传）。 */
  onNavigate?: () => void;
}

export function SidebarPlanTree({ plan, onNavigate }: SidebarPlanTreeProps) {
  const { t } = useTranslation("plans");
  // 默认展开全部阶段：侧栏的全部意义就是"看见进度"，收起来等于没有
  const [collapsed, setCollapsed] = useState<ReadonlySet<number>>(() => new Set());

  const percent = plan.progress?.percent ?? 0;

  const toggle = (stageId: number) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(stageId)) next.delete(stageId);
      else next.add(stageId);
      return next;
    });
  };

  return (
    <div className="plan-tree">
      <div className="plan-tree__head">
        <Link className="plan-tree__plan" to={`/plans/${plan.id}`} onClick={onNavigate}>
          {plan.title}
        </Link>
        <Link className="plan-tree__switch" to="/plans" onClick={onNavigate}>
          {t("tree.switch")}
        </Link>
        <ProgressBar
          value={percent}
          height={4}
          valueLabel={t("tree.progress", { percent: Math.round(percent) })}
        />
      </div>

      {plan.stages.length === 0 ? (
        <p className="plan-tree__empty">{t("plan.outline.empty")}</p>
      ) : (
        <ul className="plan-tree__stages">
          {plan.stages.map((stage) => {
            const isCollapsed = collapsed.has(stage.id);
            return (
              <li key={stage.id} className="plan-tree__stage">
                <button
                  type="button"
                  className="plan-tree__stage-head"
                  onClick={() => toggle(stage.id)}
                  aria-expanded={!isCollapsed}
                >
                  {isCollapsed ? (
                    <ChevronRight size={13} aria-hidden="true" />
                  ) : (
                    <ChevronDown size={13} aria-hidden="true" />
                  )}
                  <span className="plan-tree__stage-title">{stage.title}</span>
                  <span className="plan-tree__count tnum">{stage.items.length}</span>
                </button>

                {isCollapsed ? null : (
                  <ul className="plan-tree__items">
                    {stage.items.map((item) => (
                      <li key={item.id}>
                        <TreeItemLink planId={plan.id} item={item} onNavigate={onNavigate} />
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

interface TreeItemLinkProps {
  planId: number;
  item: ItemRead;
  onNavigate?: () => void;
}

function TreeItemLink({ planId, item, onNavigate }: TreeItemLinkProps) {
  const { t } = useTranslation("plans");
  const openable = item.resource_slug !== null && item.resource_available;
  const title = item.resolved_title ?? item.title;

  const body = (
    <>
      <StatusIcon status={item.status} />
      <span className="plan-tree__item-title">{title}</span>
    </>
  );

  if (!openable) {
    return (
      <span className="plan-tree__item is-static" title={t("plan.item.custom")}>
        {body}
      </span>
    );
  }

  return (
    <PlanItemLink planId={planId} slug={item.resource_slug as string} onNavigate={onNavigate}>
      {body}
    </PlanItemLink>
  );
}

/** 单独抽出来只为了让 `useMatch` 只在该链接上调用：判断"是不是当前正在读的条目"。 */
function PlanItemLink({
  planId,
  slug,
  onNavigate,
  children,
}: {
  planId: number;
  slug: string;
  onNavigate?: () => void;
  children: ReactNode;
}) {
  const match = useMatch(`/plans/${planId}/items/*`);
  const href = `/plans/${planId}/items/${slug}`;
  const active = match?.params["*"] === slug;
  return (
    <Link
      className={active ? "plan-tree__item is-active" : "plan-tree__item"}
      to={href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
    >
      {children}
    </Link>
  );
}

/** 三态图标（§9.1：待学 ○ / 完成 ✓ / 跳过 ⊘）。 */
function StatusIcon({ status }: { status: ItemRead["status"] }) {
  if (status === "done") {
    return <CheckCircle2 size={13} className="plan-tree__icon is-done" aria-hidden="true" />;
  }
  if (status === "skipped") {
    return <MinusCircle size={13} className="plan-tree__icon is-skipped" aria-hidden="true" />;
  }
  return <Circle size={13} className="plan-tree__icon is-todo" aria-hidden="true" />;
}
