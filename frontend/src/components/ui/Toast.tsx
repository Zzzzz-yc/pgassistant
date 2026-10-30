/**
 * Toast（需求文档 §9.4「容器与浮层」+ §9.6 状态提示）。
 *
 * 组件只画**一条**消息，不持有队列：队列在 `lib/query.ts`（`pushToast` / `dismissToast`），
 * `components/layout/ToastHost.tsx` 把队列映射成这里的 props。这样 ui 层保持"只吃 props、
 * 不发请求、不读全局状态"。
 *
 * 五态：
 * - hover：不改变外观（Toast 不是可点目标），只有关闭键有 hover 反馈
 * - focus-visible：落在关闭键上（2px 环）
 * - active：关闭键的按下态
 * - disabled：**不适用**（Toast 没有禁用语义）
 * - loading：**不适用**（"正在发生"用 `kind="info"` + `hint`；这里不臆造进度语义）
 */
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "./cn";

export type ToastKind = "info" | "success" | "warn" | "error";

export interface ToastProps {
  kind: ToastKind;
  message: ReactNode;
  hint?: ReactNode;
  onDismiss?: () => void;
  /** 关闭键的 aria-label，默认「知道了」。 */
  dismissLabel?: string;
  className?: string;
}

const ICONS = {
  info: Info,
  success: CheckCircle2,
  warn: AlertTriangle,
  error: XCircle,
} as const;

export function Toast({ kind, message, hint, onDismiss, dismissLabel = "知道了", className }: ToastProps) {
  const Icon = ICONS[kind];

  return (
    <div className={cn("ui-toast", `ui-toast--${kind}`, className)}>
      <Icon size={16} className="ui-toast__icon" aria-hidden="true" />
      <div className="ui-toast__body">
        <div className="ui-toast__message">{message}</div>
        {hint ? <div className="ui-toast__hint">{hint}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          className="ui-toast__close"
          aria-label={dismissLabel}
          onClick={onDismiss}
        >
          <X size={14} aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

/** 画廊/静态预览用的纵向堆栈容器；生产用 layout 的 `.toast-host`（fixed 定位）。 */
export function ToastStack({ children }: { children: ReactNode }) {
  return <div className="ui-toast-stack">{children}</div>;
}
