/**
 * ErrorState（业务组件，需求文档 §9.6「Error：错误码 + 人话 + 重试 + 复制错误详情」）。
 *
 * 口径：
 * - 「人话」来自 `i18n/locales/<lang>/errors.json`（§9.9「错误码 → 文案映射集中一处，
 *   禁止散落 if」）；这里只调 `useErrorMessage()`，不自己写 if 链。
 * - `code` 可以是后端码、也可以是 D20 的前端本地码（`NETWORK_ERROR` / `SSE_PARSE_ERROR`
 *   / `HTTP_<status>`），未登记的码由 i18n 兜底到 `UNKNOWN`。
 * - 复制的是 **JSON 详情**（含 request_id，§9.9「5xx 必须带 request_id」），不是截图。
 *
 * 五态：hover / focus-visible / active / disabled / loading **不适用**——本组件是错误展示，
 * 交互全在两个按钮上（Button 自带五态）。重试进行中由调用方把 `retrying` 传给按钮。
 */
import { AlertTriangle, Copy, RotateCcw } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button, cn } from "@/components/ui";
import { useErrorMessage } from "@/i18n";

export interface ErrorStateProps {
  /** 错误码（后端码或 D20 的前端本地码）。 */
  code?: string | null;
  /** 覆盖 i18n 的「人话」文案。 */
  message?: ReactNode;
  /** 建议动作的一句话（默认取 i18n 的 action）。 */
  hint?: ReactNode;
  /** 原始 detail / 错误对象；复制详情时序列化它。 */
  detail?: unknown;
  requestId?: string | null;
  title?: string;
  onRetry?: () => void;
  /** 重试请求进行中（按钮 loading）。 */
  retrying?: boolean;
  retryLabel?: string;
  copyLabel?: string;
  copiedLabel?: string;
  /** 显示可折叠的详情块（默认只在复制时用，不占版面）。 */
  showDetail?: boolean;
  center?: boolean;
  className?: string;
}

function serialize(props: ErrorStateProps): string {
  const payload = {
    code: props.code ?? "UNKNOWN",
    message: typeof props.message === "string" ? props.message : undefined,
    hint: typeof props.hint === "string" ? props.hint : undefined,
    request_id: props.requestId ?? null,
    detail: props.detail ?? null,
  };
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload.code);
  }
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 非安全上下文/被策略拒绝：落到下面的兜底
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

export function ErrorState({
  code,
  message,
  hint,
  detail,
  requestId,
  title = "出了点问题",
  onRetry,
  retrying = false,
  retryLabel = "重试",
  copyLabel = "复制错误详情",
  copiedLabel = "已复制",
  showDetail = false,
  center = false,
  className,
}: ErrorStateProps) {
  const humanMessage = useErrorMessage();
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const resolvedCode = code ?? "UNKNOWN";
  const resolvedMessage = message ?? humanMessage(resolvedCode);

  return (
    <div
      className={cn("error-state", center && "error-state--center", className)}
      role="alert"
      aria-live="assertive"
    >
      <div className="error-state__head">
        <AlertTriangle size={16} aria-hidden="true" />
        <span className="error-state__title">{title}</span>
        <span className="error-state__code" title="错误码">
          {resolvedCode}
        </span>
      </div>

      <p className="error-state__message">{resolvedMessage}</p>
      {hint ? <p className="error-state__hint">{hint}</p> : null}

      <div className="error-state__actions">
        {onRetry ? (
          <Button
            size="sm"
            variant="secondary"
            loading={retrying}
            iconLeft={<RotateCcw size={13} />}
            onClick={onRetry}
          >
            {retryLabel}
          </Button>
        ) : null}

        <Button
          size="sm"
          variant="ghost"
          iconLeft={<Copy size={13} />}
          onClick={() => {
            void copyText(serialize({ code, message, hint, detail, requestId })).then((ok) =>
              setCopyState(ok ? "copied" : "failed"),
            );
          }}
        >
          {copyState === "copied" ? copiedLabel : copyState === "failed" ? "复制失败" : copyLabel}
        </Button>

        {requestId ? <span className="error-state__hint">request_id: {requestId}</span> : null}
      </div>

      {showDetail ? <pre className="error-state__detail">{serialize({ code, message, hint, detail, requestId })}</pre> : null}
    </div>
  );
}
