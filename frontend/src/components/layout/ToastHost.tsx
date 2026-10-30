/**
 * Toast 出口（Owner：前端骨架 M0；M2 组件库把它换成 `ui/Toast` 的视觉）。
 *
 * 队列仍在 `lib/query.ts`（`subscribeToasts` / `getToasts` / `dismissToast`），
 * `useToast()` 的 API 一个字都没改；这里只负责把队列映射成展示组件。
 * 定位仍用 `layout.css` 的 `.toast-host`（右下角、`--z-toast`）。
 */
import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

import { Toast } from "@/components/ui";
import { dismissToast, getToasts, subscribeToasts } from "@/lib/query";

export function ToastHost() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, getToasts);
  const { t } = useTranslation("common");

  if (items.length === 0) return null;

  return (
    <div className="toast-host" role="status" aria-live="polite">
      {items.map((item) => (
        <Toast
          key={item.id}
          kind={item.kind}
          message={item.message}
          hint={item.hint}
          onDismiss={() => dismissToast(item.id)}
          dismissLabel={t("action.dismiss")}
        />
      ))}
    </div>
  );
}
