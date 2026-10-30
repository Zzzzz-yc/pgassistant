/**
 * App（Owner：前端骨架 M0）：只做 Provider 装配，不放布局与业务。
 * 布局在 `components/layout/AppShell.tsx`，路由在 `router.tsx`。
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { queryClient } from "@/lib/query";
import { router } from "@/router";

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
