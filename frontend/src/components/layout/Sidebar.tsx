/**
 * 侧栏（Owner：前端骨架 M0，§9.1）。
 *
 * - 无激活计划时：按 `/api/taxonomy` 的 stack 展示章节树占位（只做导航入口，
 *   真正的筛选器是 M2 的 FilterBar）。
 * - 有计划时：使用 `planTree` 插槽，由计划模块（M3）注入阶段 + 条目树。
 *   插槽在 M0 恒为空，因此运行期永远走章节树分支。
 */
import { ChevronLeft, ChevronRight, Layers } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { useTaxonomy } from "@/lib/query";

export interface SidebarProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  mobileOpen: boolean;
  /** 计划树插槽：M3 注入。为 null 时回退到章节树。 */
  planTree?: ReactNode | null;
  /** 插槽标题（M3 用计划命名空间的 `tree.title`；不传沿用 nav 的占位文案）。 */
  planTreeLabel?: ReactNode;
  /** 点侧栏里的链接后调用（移动端用来关抽屉）。 */
  onNavigate?: () => void;
}

export function Sidebar({
  collapsed,
  onToggleCollapsed,
  mobileOpen,
  planTree = null,
  planTreeLabel,
  onNavigate,
}: SidebarProps) {
  const { t } = useTranslation("nav");
  const taxonomy = useTaxonomy();

  const className = [
    "sidebar",
    collapsed ? "is-collapsed" : "",
    mobileOpen ? "is-open" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <aside className={className} aria-label={t("chapters")}>
      <div className="sidebar__header">
        <span className="sidebar__title">
          {planTree ? (planTreeLabel ?? t("planTreeSlot")) : t("chapters")}
        </span>
        <button
          type="button"
          className="sidebar__toggle"
          onClick={onToggleCollapsed}
          aria-label={t("toggleSidebar")}
          aria-expanded={!collapsed}
        >
          {collapsed ? <ChevronRight size={16} aria-hidden="true" /> : <ChevronLeft size={16} aria-hidden="true" />}
        </button>
      </div>

      <div className="sidebar__body">
        {planTree ? (
          planTree
        ) : (
          <ChapterTree taxonomy={taxonomy} onNavigate={onNavigate} />
        )}
      </div>
    </aside>
  );
}

interface ChapterTreeProps {
  taxonomy: ReturnType<typeof useTaxonomy>;
  onNavigate?: () => void;
}

function ChapterTree({ taxonomy, onNavigate }: ChapterTreeProps) {
  const { t } = useTranslation("nav");
  const { data, isLoading, isError } = taxonomy;

  if (isLoading) {
    return (
      <div className="sidebar__section" aria-hidden="true">
        <span className="skeleton" style={{ width: "80%" }} />
        <span className="skeleton" style={{ width: "60%", marginTop: "8px" }} />
      </div>
    );
  }

  if (isError || !data) {
    return <p className="sidebar__footnote">{t("noTaxonomy")}</p>;
  }

  return (
    <>
      {data.stacks.map((stack) => (
        <div className="sidebar__section" key={stack.stack}>
          <Link
            className="sidebar__item sidebar__stack"
            to={`/library?stack=${encodeURIComponent(stack.stack)}`}
            onClick={onNavigate}
          >
            <Layers size={13} aria-hidden="true" style={{ display: "inline", marginRight: "4px" }} />
            {stack.label?.zh ?? stack.stack}
            <span className="sidebar__count tnum">{stack.total}</span>
          </Link>
          {stack.categories.map((category) => (
            <span className="sidebar__item" key={`${stack.stack}/${category.category}`}>
              {category.category}
              <span className="sidebar__count tnum">{category.count}</span>
            </span>
          ))}
        </div>
      ))}
      <p className="sidebar__footnote">{t("chaptersHint")}</p>
    </>
  );
}
