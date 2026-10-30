/**
 * CommandPalette（业务组件，需求文档 §9.4「容器与浮层」+ §9.5 首页大搜索、
 * §11「POST /api/search 命令面板」，API.md §3.7）。
 *
 * 行为契约：
 * - Ctrl/⌘+K 的**绑定**在布局层（`components/layout/AppShell.tsx` 的 `useHotkeys`），
 *   本组件只负责"已打开"之后的输入、检索、键盘选择与关闭；
 * - 输入防抖（`lib/query.ts` 的 `useDebouncedValue`，默认 250ms）→ `POST /api/search`
 *   body `{q, limit: 8}`；接口没实现时**展示错误态**（ErrorState + 重试），不崩；
 * - ↑ / ↓ 选择、Enter 打开第一条（或当前高亮）、Esc 关闭；
 * - 零结果给「去资料库搜索」出口（§9.9 搜索零结果三动作里的第一条）；
 * - 允许注入 `onSearch`（资料页后续可换成自己的 `searchPost`），也允许注入
 *   `onNavigate` / `onSearchInLibrary` 以接入 SPA 路由。
 *
 * 焦点：面板用 Radix Dialog 包（`ui/Dialog`）→ 打开时锁焦点、Esc 关闭、
 * 关闭后焦点回到触发元素（§9.7），不用手写 trap。
 *
 * 五态：
 * - hover：结果行底色 `--bg`
 * - focus-visible：输入框 2px 环；结果行由 `aria-activedescendant` 高亮（不真移焦点）
 * - active：结果行 `--primary-soft-hover`
 * - disabled / loading：**禁用态不适用**（面板要么开着要么关着）；检索中由
 *   `status=loading` 的三行 Skeleton 表达，输入框不阻塞（可继续打字）
 */
import { CornerDownLeft, Search, SearchX } from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { Button, Dialog, Skeleton } from "@/components/ui";
import { ApiError, request } from "@/lib/http";
import { useDebouncedValue } from "@/lib/query";
import type { SearchHit, SearchResponse } from "@/types/api";

import { ErrorState } from "./ErrorState";
import "./business.css";

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  /** 自定义检索（默认 `POST /api/search`）。 */
  onSearch?: (query: string, signal: AbortSignal) => Promise<SearchResponse>;
  /** 打开某条资料（默认整页跳到 `/library/:slug`）。 */
  onNavigate?: (slug: string) => void;
  /** 零结果时的"去资料库搜索"（默认整页跳到 `/library?q=`）。 */
  onSearchInLibrary?: (query: string) => void;
  debounceMs?: number;
  /** 结果条数，默认 8（契约上限 50，命令面板不需要那么多）。 */
  limit?: number;
  className?: string;
}

type PaletteState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: SearchResponse }
  | { status: "error"; error: ApiError };

function toApiError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause;
  return new ApiError("NETWORK_ERROR", "连不上本地服务", 0, { detail: String(cause) });
}

/** 把片段里的 `[命中词]` 标记渲染成 `<mark>`（纯文本切分，不拼 HTML，§13.2-2）。 */
function HighlightedSnippet({ text }: { text: string }): ReactNode {
  const parts = text.split(/\[([^\]]+)\]/g);
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <mark key={index} className="cp__mark">
            {part}
          </mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

function HitRow({
  hit,
  index,
  active,
  onPick,
  onHover,
}: {
  hit: SearchHit;
  index: number;
  active: boolean;
  onPick: () => void;
  onHover: () => void;
}) {
  return (
    <li role="none">
      <button
        type="button"
        id={`cp-hit-${index}`}
        role="option"
        aria-selected={active}
        className="cp__hit"
        tabIndex={-1}
        onMouseEnter={onHover}
        onMouseDown={(event) => {
          // 别让输入框先失焦，否则结果列表会在点击生效前收起
          event.preventDefault();
          onPick();
        }}
      >
        <span className="cp__hit-title">{hit.title}</span>
        <span className="cp__hit-snippet">
          <HighlightedSnippet text={hit.snippet} />
        </span>
        <span className="cp__hit-meta">
          {hit.stack} / {hit.category}
        </span>
      </button>
    </li>
  );
}

export function CommandPalette({
  open,
  onClose,
  onSearch,
  onNavigate,
  onSearchInLibrary,
  debounceMs = 250,
  limit = 8,
  className,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [retryToken, setRetryToken] = useState(0);
  const [state, setState] = useState<PaletteState>({ status: "idle" });
  const inputRef = useRef<HTMLInputElement>(null);

  const debounced = useDebouncedValue(query, debounceMs);
  const term = debounced.trim();

  // 检索函数放 ref：调用方传内联箭头函数也不会让 effect 反复触发
  const defaultSearch = (q: string, signal: AbortSignal) =>
    request<SearchResponse>("POST", "/api/search", { body: { q, limit }, signal });
  const searchRef = useRef(defaultSearch);
  searchRef.current = onSearch ?? defaultSearch;

  const hits = state.status === "success" ? state.data.items : [];

  useEffect(() => {
    if (!open) return;
    if (!term) {
      setState({ status: "idle" });
      return;
    }

    const controller = new AbortController();
    setState({ status: "loading" });

    void searchRef
      .current(term, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setState({ status: "success", data });
        setActiveIndex(0);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setState({ status: "error", error: toApiError(cause) });
      });

    return () => controller.abort();
  }, [term, open, retryToken]);

  // 关闭时清空，下次打开是干净状态
  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
      return;
    }
    setQuery("");
    setState({ status: "idle" });
    setActiveIndex(0);
  }, [open]);

  const goToResource = useMemo(
    () => (slug: string) => {
      if (onNavigate) {
        onNavigate(slug);
        return;
      }
      window.location.assign(`/library/${slug}`);
    },
    [onNavigate],
  );

  const goToLibrary = useMemo(
    () => (keyword: string) => {
      if (onSearchInLibrary) {
        onSearchInLibrary(keyword);
        return;
      }
      window.location.assign(`/library?q=${encodeURIComponent(keyword)}`);
    },
    [onSearchInLibrary],
  );

  function pick(index: number) {
    const hit = hits[index];
    if (!hit) return;
    onClose();
    goToResource(hit.slug);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (hits.length === 0) return;
      setActiveIndex((index) => (index + 1) % hits.length);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (hits.length === 0) return;
      setActiveIndex((index) => (index - 1 + hits.length) % hits.length);
      return;
    }
    if (event.key === "Enter") {
      if (hits.length === 0) return;
      event.preventDefault();
      pick(activeIndex);
      return;
    }
    if (event.key === "Escape") {
      // Dialog 也会处理 Esc；这里显式收一次，保证焦点先回到输入框再关闭
      event.preventDefault();
      onClose();
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      hideClose
      ariaLabel="搜索资料"
      width={640}
      className={className ? `cp ${className}` : "cp"}
    >
      <div className="cp__search">
        <Search size={16} aria-hidden="true" className="cp__search-icon" />
        <input
          ref={inputRef}
          className="cp__input"
          type="text"
          role="combobox"
          aria-expanded={state.status === "success"}
          aria-controls={state.status === "success" ? "cp-results-list" : undefined}
          aria-autocomplete="list"
          aria-activedescendant={hits.length > 0 ? `cp-hit-${activeIndex}` : undefined}
          aria-label="搜索资料"
          placeholder="搜索资料：闭包 / 动态规划 / 事件循环…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
          autoComplete="off"
          spellCheck={false}
        />
        <kbd className="cp__kbd">Esc</kbd>
      </div>

      <div className="cp__body">
        {state.status === "idle" ? (
          <div className="cp__hint">
            <p>输入关键词后按 Enter 打开第一条结果。</p>
            <p className="cp__hint-sub">↑ ↓ 选择 · Enter 打开 · Esc 关闭</p>
          </div>
        ) : null}

        {state.status === "loading" ? (
          <div className="cp__loading" aria-busy="true" aria-live="polite">
            <Skeleton shape="text" width="70%" />
            <Skeleton shape="text" width="90%" />
            <Skeleton shape="text" width="52%" />
          </div>
        ) : null}

        {state.status === "error" ? (
          <ErrorState
            code={state.error.code}
            message={state.error.message}
            hint={state.error.hint}
            requestId={state.error.requestId ?? null}
            retrying={false}
            onRetry={() => setRetryToken((token) => token + 1)}
            retryLabel="重试检索"
            title="检索失败"
            className="cp__error"
          />
        ) : null}

        {state.status === "success" && hits.length === 0 ? (
          <div className="cp__empty">
            <SearchX size={18} aria-hidden="true" />
            <p>没有匹配「{term}」的资料</p>
            <Button size="sm" variant="secondary" onClick={() => goToLibrary(term)}>
              去资料库搜索
            </Button>
          </div>
        ) : null}

        {state.status === "success" && hits.length > 0 ? (
          <ul className="cp__list" id="cp-results-list" role="listbox" aria-label="搜索结果">
            {hits.map((hit, index) => (
              <HitRow
                key={hit.slug}
                hit={hit}
                index={index}
                active={index === activeIndex}
                onHover={() => setActiveIndex(index)}
                onPick={() => pick(index)}
              />
            ))}
          </ul>
        ) : null}
      </div>

      <div className="cp__footer">
        <span>
          <CornerDownLeft size={12} aria-hidden="true" /> Enter 打开
        </span>
        <span>↑ ↓ 选择</span>
        <span className="cp__footer-end">
          {state.status === "success" ? `${state.data.total} 条结果` : null}
        </span>
      </div>
    </Dialog>
  );
}
