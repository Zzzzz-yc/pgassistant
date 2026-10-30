/**
 * AppShell（Owner：前端骨架 M0，§9.1）：顶栏 56px + 侧栏 260px（可折叠 56px）+ 内容区。
 *
 * 这里装配的四件事，后续模块直接复用、不要各写一套：
 * 1. 路由出口 `<Outlet/>` 包在 Suspense 里（页面都是 React.lazy）；
 * 2. Ctrl/⌘+K 打开命令面板挂载点；
 * 3. `app:reload-required`（403 TOKEN_INVALID）→ 常驻 Toast 提示重新加载；
 * 4. Toast 出口。
 */
import { Suspense, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useLocation } from "react-router-dom";

import { CommandPalette } from "@/components/layout/CommandPalette";
import { PageFallback } from "@/components/layout/PageFallback";
import { Sidebar } from "@/components/layout/Sidebar";
import { ToastHost } from "@/components/layout/ToastHost";
import { Topbar } from "@/components/layout/Topbar";
import { SidebarPlanTree } from "@/features/plans/components/SidebarPlanTree";
import { useActivePlan } from "@/features/plans/hooks";
import { errorAction, errorMessage } from "@/i18n";
import { RELOAD_REQUIRED_EVENT } from "@/lib/http";
import { pushToast, useHotkeys } from "@/lib/query";

export function AppShell() {
  const { t } = useTranslation(["nav", "common", "plans"]);
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  /**
   * 计划树插槽（§9.1）：有激活计划时，侧栏改为渲染计划树（M3 注入）。
   * 没有激活计划时传 `null`，`Sidebar` 自动回退到「章节树」分支。
   */
  const { plan: activePlan } = useActivePlan();

  const closePalette = useCallback(() => setPaletteOpen(false), []);

  useHotkeys([
    {
      combo: "mod+k",
      allowInInput: true,
      handler: () => setPaletteOpen((open) => !open),
    },
  ]);

  // 路由变化就收起移动端抽屉，避免遮挡新页面
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  // 令牌失效（服务重启）：常驻提示，直到用户刷新
  useEffect(() => {
    const onReloadRequired = () => {
      pushToast({
        kind: "error",
        message: errorMessage("TOKEN_INVALID"),
        hint: errorAction("TOKEN_INVALID"),
        ttlMs: 0,
      });
    };
    window.addEventListener(RELOAD_REQUIRED_EVENT, onReloadRequired);
    return () => window.removeEventListener(RELOAD_REQUIRED_EVENT, onReloadRequired);
  }, []);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        {t("common:action.skipToContent")}
      </a>

      <Topbar onOpenSearch={() => setPaletteOpen(true)} onOpenMenu={() => setMobileOpen(true)} />

      <div className="app-shell__body">
        <Sidebar
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((value) => !value)}
          mobileOpen={mobileOpen}
          planTreeLabel={t("plans:tree.title")}
          planTree={
            activePlan ? (
              <SidebarPlanTree plan={activePlan} onNavigate={() => setMobileOpen(false)} />
            ) : null
          }
          onNavigate={() => setMobileOpen(false)}
        />

        {mobileOpen ? (
          <div
            className="sidebar__scrim"
            aria-hidden="true"
            onClick={() => setMobileOpen(false)}
          />
        ) : null}

        <main className="app-main" id="main">
          <div className="app-main__inner">
            <Suspense fallback={<PageFallback label={t("common:state.loading")} />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={closePalette} />
      <ToastHost />
    </div>
  );
}
