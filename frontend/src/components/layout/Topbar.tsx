/**
 * 顶栏（Owner：前端骨架 M0，§9.1）：
 * 品牌 + 搜索触发（Ctrl/⌘+K）+ 三个入口（资料库 / 助手 / 设置）。
 * 计划切换不放这里——它在侧栏顶部的计划名下拉里（M3）。
 */
import { BookOpen, Library, Menu, MessageSquare, Search, Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, NavLink } from "react-router-dom";

export interface TopbarProps {
  onOpenSearch: () => void;
  onOpenMenu: () => void;
}

const IS_MAC = typeof navigator !== "undefined" && /mac|iphone|ipad/i.test(navigator.platform);
const SEARCH_HINT = IS_MAC ? "⌘K" : "Ctrl K";

const NAV_ITEMS = [
  { to: "/library", labelKey: "library", Icon: Library },
  { to: "/assistant", labelKey: "assistant", Icon: MessageSquare },
  { to: "/settings", labelKey: "settings", Icon: Settings },
] as const;

export function Topbar({ onOpenSearch, onOpenMenu }: TopbarProps) {
  const { t } = useTranslation("nav");

  return (
    <header className="topbar">
      <button
        type="button"
        className="topbar__icon-button"
        onClick={onOpenMenu}
        aria-label={t("openMenu")}
      >
        <Menu size={18} aria-hidden="true" />
      </button>

      <Link to="/" className="topbar__brand">
        <BookOpen size={18} className="topbar__brand-mark" aria-hidden="true" />
        {t("brand")}
      </Link>

      <button
        type="button"
        className="topbar__search"
        onClick={onOpenSearch}
        aria-label={t("openSearch")}
        aria-keyshortcuts="Meta+K Control+K"
      >
        <Search size={15} aria-hidden="true" />
        <span className="topbar__search-label">{t("searchTrigger")}</span>
        <kbd className="topbar__kbd">{SEARCH_HINT}</kbd>
      </button>

      <nav className="topbar__actions" aria-label={t("search")}>
        {NAV_ITEMS.map(({ to, labelKey, Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => `topbar__link${isActive ? " is-active" : ""}`}
          >
            <Icon size={15} aria-hidden="true" />
            <span className="topbar__link-label">{t(labelKey)}</span>
          </NavLink>
        ))}
      </nav>
    </header>
  );
}
