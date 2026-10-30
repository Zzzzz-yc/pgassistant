/**
 * 路由表（Owner：前端骨架 M0）。
 *
 * **逐行照 docs/contracts/MODULES.md §3.1**，不得自行增删：
 *   /、/library、/library/*、/plans、/plans/new、/plans/:planId、
 *   /plans/:planId/items/*、/assistant、/settings、*
 *
 * 两条硬规则：
 * 1. 资料用 **v6 splat**：路谱写 `library/*` / `plans/:planId/items/*`，
 *    页面里从 `useParams()["*"]` 取完整 slug（可含 `/`，D2）。
 *    注意 v6 **不支持** v5 的命名 splat `:slug(*)`——那样写只会匹配一个字面量段，
 *    `/library/a/b/c` 会掉进 `*` 404（M0 缺陷，已由本文件修正）。
 *    计划用整数 `:planId`（D12），二者不能混。
 * 2. 页面一律 `React.lazy`：首屏只加载壳，各模块的 chunk 进页才拉。
 */
import { lazy } from "react";
import { createBrowserRouter, type RouteObject } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { RouteErrorBoundary } from "@/components/layout/ErrorBoundary";

const HomePage = lazy(() => import("@/pages/home"));
const LibraryPage = lazy(() => import("@/pages/library"));
const ResourcePage = lazy(() => import("@/pages/library/ResourcePage"));
const PlansPage = lazy(() => import("@/pages/plans"));
const NewPlanPage = lazy(() => import("@/pages/plans/NewPlanPage"));
const PlanPage = lazy(() => import("@/pages/plans/PlanPage"));
const PlanItemPage = lazy(() => import("@/pages/plans/PlanItemPage"));
const AssistantPage = lazy(() => import("@/pages/assistant"));
const SettingsPage = lazy(() => import("@/pages/settings"));
const NotFoundPage = lazy(() => import("@/pages/NotFound"));

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "library", element: <LibraryPage /> },
      // v6 splat：`library/*` 吃掉整段路径，页面用 useParams()["*"] 取 slug
      { path: "library/*", element: <ResourcePage /> },
      { path: "plans", element: <PlansPage /> },
      { path: "plans/new", element: <NewPlanPage /> },
      { path: "plans/:planId", element: <PlanPage /> },
      { path: "plans/:planId/items/*", element: <PlanItemPage /> },
      { path: "assistant", element: <AssistantPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
