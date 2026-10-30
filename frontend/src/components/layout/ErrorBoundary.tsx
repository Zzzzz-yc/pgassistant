/**
 * 错误边界（Owner：前端骨架 M0）。
 *
 * 两个出口：
 * - `RouteErrorBoundary`：交给 react-router 的 `errorElement`，捕获路由渲染/懒加载失败；
 * - `ErrorBoundary`：普通类组件，M2 的 `ErrorState` 落地后这里只做兜底，不追求视觉。
 *
 * 纪律：只展示 `code` + 人话 + 详情（§9.6），**不吞异常**（仍打印到 console 便于排查）。
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useRouteError } from "react-router-dom";

import { errorAction, errorMessage } from "@/i18n";
import { ApiError } from "@/lib/http";

function describe(error: unknown): { code: string; message: string; action: string; detail: string } {
  if (error instanceof ApiError) {
    return {
      code: error.code,
      message: errorMessage(error.code),
      action: errorAction(error.code),
      detail: JSON.stringify(
        { code: error.code, message: error.message, detail: error.detail, hint: error.hint },
        null,
        2,
      ),
    };
  }
  if (error instanceof Error) {
    return {
      code: error.name || "ERROR",
      message: errorMessage(null),
      action: errorAction(null),
      detail: `${error.name}: ${error.message}\n${error.stack ?? ""}`,
    };
  }
  return { code: "UNKNOWN", message: errorMessage(null), action: errorAction(null), detail: String(error) };
}

export function ErrorPanel({ error }: { error: unknown }) {
  const info = describe(error);
  return (
    <section className="error-boundary" role="alert">
      <h1 className="page__title">页面出错了</h1>
      <p className="page__status">{info.message}</p>
      <p className="error-boundary__code">{info.code}</p>
      <details>
        <summary>{info.action}</summary>
        <pre className="error-boundary__detail">{info.detail}</pre>
      </details>
      <div className="error-boundary__actions">
        <button type="button" className="button button--primary" onClick={() => window.location.reload()}>
          重新加载
        </button>
        <button
          type="button"
          className="button"
          onClick={() => {
            void navigator.clipboard?.writeText(info.detail);
          }}
        >
          复制错误详情
        </button>
      </div>
    </section>
  );
}

/** 路由级错误元素：react-router 会把抛出的异常丢进 useRouteError。 */
export function RouteErrorBoundary() {
  const error = useRouteError();
  return <ErrorPanel error={error} />;
}

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: unknown;
}

/** 普通错误边界：局部包住某个页面，避免一处异常拖垮整个壳。 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    // 不吞异常：控制台留一份，方便把 request_id 与堆栈对上
    console.error("[pgassistant] 页面渲染异常", error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.error) return <ErrorPanel error={this.state.error} />;
    return this.props.children;
  }
}
