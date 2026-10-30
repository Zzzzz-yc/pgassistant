/**
 * Toast 出口（Owner：前端骨架 M0）。
 * 只负责渲染 `lib/query.ts` 里的 Toast 存储；业务模块用 `useToast().push(...)`。
 */
import { X } from "lucide-react";
import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

import { dismissToast, getToasts, subscribeToasts } from "@/lib/query";

export function ToastHost() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, getToasts);
  const { t } = useTranslation("common");

  if (items.length === 0) return null;

  return (
    <div className="toast-host" role="status" aria-live="polite">
      {items.map((item) => (
        <div key={item.id} className={`toast toast--${item.kind}`}>
          <div className="toast__body">
            <div className="toast__message">{item.message}</div>
            {item.hint ? <div className="toast__hint">{item.hint}</div> : null}
          </div>
          <button
            type="button"
            className="toast__close"
            aria-label={t("action.dismiss")}
            onClick={() => dismissToast(item.id)}
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  );
}
