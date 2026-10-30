/**
 * CodeRunnerPanel（需求文档 §20.4「UI」、§9.5「抽屉与目录共用右侧空间」、§9.4 业务组件）。
 *
 * 版式两条：
 * - **桌面（≥768px）**：右侧固定抽屉，默认 480px，左边缘可拖拽调宽（360–760px，且不超过
 *   视口 −320px），不遮顶栏；非模态，正文仍可滚动与阅读；
 * - **手机（<768px）**：改用组件库的 `Sheet`（全屏）。
 * 两种形态共用同一份主体：语言徽章 + 运行按钮 + CodeMirror + 输出控制台 + 耗时/内存。
 *
 * 状态口径：
 * - 关闭即 `reset()`：清空输出与耗时，不记历史、刷新不恢复（§20.4 最后一条）；
 * - 首次打开给一张说明卡「代码在你本机浏览器内运行，不会上传，也不经过模型」，可勾「不再提示」；
 * - `Esc` 关闭、关闭后焦点回到触发元素（§9.7）。
 */
import { Play, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { Badge, Button, Checkbox, IconButton, Sheet } from "@/components/ui";

import { langLabel } from "../languages";
import type { RunnerLang } from "../protocol";
import { acknowledgeIntro, isIntroAcknowledged, useRunnerEnabled } from "../settings";
import { useRunner } from "../useRunner";
import { CodeEditor, type CodeEditorHandle } from "./CodeEditor";
import { OutputConsole } from "./OutputConsole";
import "../runner.css";

export interface CodeRunnerPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  lang: RunnerLang;
  /** 代码块原文，作为编辑器初值。 */
  initialCode: string;
  /** 资料标题（标题栏与无障碍标签用）。 */
  resourceTitle?: string;
}

const DEFAULT_WIDTH = 480;
const MIN_WIDTH = 360;
const MAX_WIDTH = 760;
const MOBILE_QUERY = "(max-width: 767px)";

/** 手机断点（§9.8：<768 手机）。用 matchMedia 而不是 window.innerWidth，避免滚动时抖动。 */
function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    const query = window.matchMedia(MOBILE_QUERY);
    const onChange = (event: MediaQueryListEvent) => setMobile(event.matches);
    setMobile(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return mobile;
}

export function CodeRunnerPanel({
  open,
  onOpenChange,
  lang,
  initialCode,
  resourceTitle,
}: CodeRunnerPanelProps) {
  const mobile = useIsMobile();
  const { enabled } = useRunnerEnabled();
  const { snapshot, run, reset } = useRunner();
  const editorRef = useRef<CodeEditorHandle>(null);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [dirty, setDirty] = useState(false);
  const [introAcked, setIntroAcked] = useState(isIntroAcknowledged);
  const [ackChecked, setAckChecked] = useState(false);
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;

  const busy = snapshot.status === "loading" || snapshot.status === "running";

  const close = useCallback(() => {
    reset();
    onOpenChangeRef.current(false);
  }, [reset]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        reset();
      }
      onOpenChangeRef.current(next);
    },
    [reset],
  );

  const runCode = useCallback(() => {
    const code = editorRef.current?.getValue() ?? initialCode;
    run(lang, code);
  }, [initialCode, lang, run]);

  const dismissIntro = useCallback(() => {
    if (ackChecked) acknowledgeIntro();
    setIntroAcked(true);
  }, [ackChecked]);

  // 焦点：打开时落到编辑器，关闭后还给触发元素（§9.7）
  useEffect(() => {
    if (!open || mobile) return;
    const previous = document.activeElement as HTMLElement | null;
    editorRef.current?.focus();
    return () => {
      if (previous && typeof previous.focus === "function") previous.focus();
    };
  }, [open, mobile]);

  // Esc 关闭（桌面抽屉是非模态，Radix 不管这里）
  useEffect(() => {
    if (!open || mobile) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close();
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [open, mobile, close]);

  const startResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const maxByViewport = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, window.innerWidth - 320));

    const onMove = (moveEvent: PointerEvent) => {
      const next = startWidth + (startX - moveEvent.clientX);
      setWidth(Math.min(Math.max(next, MIN_WIDTH), maxByViewport));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, [width]);

  const onHandleKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    setWidth((prev) => {
      const next = event.key === "ArrowLeft" ? prev + 24 : prev - 24;
      return Math.min(Math.max(next, MIN_WIDTH), MAX_WIDTH);
    });
  }, []);

  const body = (
    <div className="runner-sheet">
      <div className="runner__toolbar">
        <Badge tone="accent">{langLabel(lang)}</Badge>
        <Button
          size="sm"
          variant="primary"
          iconLeft={<Play size={13} />}
          loading={busy}
          loadingLabel="运行中"
          onClick={runCode}
        >
          运行
        </Button>
        {dirty ? <span className="runner__dirty">已修改</span> : null}
        <span className="runner__shortcut">Ctrl/⌘ + Enter 运行</span>
      </div>

      {!introAcked ? (
        <div className="runner__intro" role="note">
          <p className="runner__intro-text">
            代码在你本机浏览器内运行，不会上传，也不经过模型。
          </p>
          <div className="runner__intro-actions">
            <Checkbox
              label="不再提示"
              checked={ackChecked}
              onCheckedChange={(next) => setAckChecked(next === true)}
            />
            <Button size="sm" variant="ghost" onClick={dismissIntro}>
              知道了
            </Button>
          </div>
        </div>
      ) : null}

      <CodeEditor
        ref={editorRef}
        value={initialCode}
        lang={lang}
        ariaLabel={`${resourceTitle ?? "资料"}的 ${langLabel(lang)} 代码`}
        onChange={() => setDirty(true)}
        onRunShortcut={runCode}
      />

      <OutputConsole
        status={snapshot.status}
        stdout={snapshot.stdout}
        stderr={snapshot.stderr}
        traceback={snapshot.traceback}
        error={snapshot.error}
        elapsedMs={snapshot.elapsedMs}
        memoryBytes={snapshot.memoryBytes}
        truncated={snapshot.truncated}
        loadingText={snapshot.loadingText}
        onJumpToLine={(line) => editorRef.current?.jumpToLine(line)}
        onRetry={snapshot.error && snapshot.error.kind !== "runtime" ? runCode : undefined}
      />
    </div>
  );

  if (!enabled) return null;

  if (mobile) {
    return (
      <Sheet
        open={open}
        onOpenChange={handleOpenChange}
        side="right"
        title={`运行 ${langLabel(lang)}`}
        closeLabel="关闭运行面板"
      >
        <div className="runner-mobile">{body}</div>
      </Sheet>
    );
  }

  if (!open) return null;

  return createPortal(
    <div
      className="runner-drawer"
      role="dialog"
      aria-modal="false"
      aria-label={`运行 ${langLabel(lang)} 代码`}
      style={{ width }}
    >
      <div
        className="runner-drawer__handle"
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动或方向键调整面板宽度"
        tabIndex={0}
        onPointerDown={startResize}
        onKeyDown={onHandleKeyDown}
      />
      <header className="runner-drawer__head">
        <span className="runner-drawer__title">运行 {langLabel(lang)}</span>
        <IconButton label="关闭运行面板" icon={<X size={16} />} size="sm" onClick={close} />
      </header>
      <div className="runner-drawer__body">{body}</div>
    </div>,
    document.body,
  );
}
