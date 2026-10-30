/**
 * OutputConsole（需求文档 §9.4「业务组件」：区分 stdout / stderr / traceback，行号高亮，
 * 超长自动截断；§20.4 输出三色 + traceback 行号可点击定位）。
 *
 * 渲染铁律（§20.5-2）：**只用 textContent**，任何输出都是文本节点，绝不 `innerHTML`——
 * 用户代码 `print("<script>alert(1)</script>")` 必须原样显示成一行字，不能被执行。
 * 本文件因此没有一处 `dangerouslySetInnerHTML`。
 *
 * 行号定位：traceback 里形如 `line 7` / `第 7 行` 的片段被切成「行号按钮」，点击回调
 * `onJumpToLine(7)`，由面板把 CodeMirror 光标移到那一行。
 */
import { AlertTriangle, CircleAlert, RotateCcw, Terminal } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui";

import type { RunnerErrorState, RunnerStatus } from "../useRunner";

export interface OutputConsoleProps {
  status: RunnerStatus;
  stdout: string;
  stderr: string;
  traceback: string | null;
  error: RunnerErrorState | null;
  elapsedMs: number | null;
  memoryBytes: number | null;
  truncated: boolean;
  /** loading 阶段的文案（预热运行时 / 缓存进度）。 */
  loadingText?: string | null;
  onJumpToLine?: (line: number) => void;
  onRetry?: () => void;
}

/** `line 7` / `第 7 行` 两种写法都识别（Python traceback 与 JS 包装后的栈各用一种）。 */
const LINE_PATTERN = /(line\s+(\d+)|第\s*(\d+)\s*行)/g;

function formatMs(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value < 1) return "<1 ms";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

function formatBytes(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value <= 0) return "—";
  const mb = value / 1024 / 1024;
  return mb >= 10 ? `${mb.toFixed(0)} MB` : `${mb.toFixed(1)} MB`;
}

/** 把一段 traceback 切成文本 + 可点行号（仍然只是文本节点，没有 HTML）。 */
function renderTraceback(text: string, onJump?: (line: number) => void): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;

  for (const match of text.matchAll(LINE_PATTERN)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    const line = Number(match[2] ?? match[3]);
    if (Number.isFinite(line) && line > 0) {
      nodes.push(
        <button
          key={`line-${key}`}
          type="button"
          className="runner-trace__line"
          onClick={() => onJump?.(line)}
          title={`定位到第 ${line} 行`}
          data-line={line}
        >
          {match[0]}
        </button>,
      );
    } else {
      nodes.push(match[0]);
    }
    cursor = start + match[0].length;
    key += 1;
  }

  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

export function OutputConsole({
  status,
  stdout,
  stderr,
  traceback,
  error,
  elapsedMs,
  memoryBytes,
  truncated,
  loadingText,
  onJumpToLine,
  onRetry,
}: OutputConsoleProps) {
  const idle = status === "idle";
  const hasAnything = Boolean(stdout || stderr || traceback || error);

  return (
    <section className="runner-out" aria-label="输出">
      <header className="runner-out__head">
        <span className="runner-out__title">
          <Terminal size={13} aria-hidden="true" /> 输出
        </span>
        <span className="runner-out__stat" title="从开始执行到结束的墙钟时间">
          耗时 {formatMs(elapsedMs)}
        </span>
        <span className="runner-out__stat" title="浏览器的 JS 堆或 Pyodide 堆占用（取不到显示 —）">
          内存 {formatBytes(memoryBytes)}
        </span>
      </header>

      <div className="runner-out__body" data-status={status}>
        {idle ? <p className="runner-out__hint">点「运行」在这里看输出。关闭抽屉即清空，不记历史。</p> : null}

        {status === "loading" ? (
          <p className="runner-out__hint">{loadingText ?? "正在准备运行时…"}</p>
        ) : null}

        {status === "running" && !hasAnything ? (
          <p className="runner-out__hint runner-out__hint--running">运行中…（超过 5 秒会被终止）</p>
        ) : null}

        {error ? (
          <div className="runner-out__error" role="alert" data-kind={error.kind}>
            <p className="runner-out__error-head">
              {error.kind === "timeout" ? (
                <AlertTriangle size={14} aria-hidden="true" />
              ) : (
                <CircleAlert size={14} aria-hidden="true" />
              )}
              <span>{error.message}</span>
            </p>
            {error.kind === "runtime-missing" || error.kind === "load" ? (
              <p className="runner-out__error-hint">
                {error.kind === "runtime-missing"
                  ? "首次加载会把 Pyodide 缓存到本机，之后离线也能用。"
                  : "运行时资源来自本机打包的 /assets/pyodide/，不是 CDN。"}
              </p>
            ) : null}
            {onRetry ? (
              <Button size="sm" variant="secondary" iconLeft={<RotateCcw size={13} />} onClick={onRetry}>
                重试
              </Button>
            ) : null}
          </div>
        ) : null}

        {stdout ? (
          <div className="runner-out__block" data-stream="stdout">
            <span className="runner-out__label">stdout</span>
            <pre className="runner-out__pre">{stdout}</pre>
          </div>
        ) : null}

        {stderr ? (
          <div className="runner-out__block" data-stream="stderr">
            <span className="runner-out__label">stderr</span>
            <pre className="runner-out__pre runner-out__pre--stderr">{stderr}</pre>
          </div>
        ) : null}

        {traceback ? (
          <div className="runner-out__block" data-stream="traceback">
            <span className="runner-out__label">traceback</span>
            <pre className="runner-out__pre runner-out__pre--trace" data-testid="runner-traceback">
              {renderTraceback(traceback, onJumpToLine)}
            </pre>
          </div>
        ) : null}

        {truncated ? (
          <p className="runner-out__truncated" role="status">
            输出超过 100 KB，已截断。
          </p>
        ) : null}
      </div>
    </section>
  );
}
