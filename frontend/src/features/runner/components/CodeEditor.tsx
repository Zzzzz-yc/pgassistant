/**
 * CodeMirror 6 编辑器（需求文档 §20.4「CodeMirror 编辑器」；§3.1 技术栈排除 Monaco）。
 *
 * 只在抽屉里用，不对外暴露成通用组件。要点：
 * - `basicSetup` 提供行号 / 历史 / 括号匹配 / 活动行高亮，样式由 runner.css 覆盖成站内 tokens；
 * - `Mod-Enter`（Ctrl/⌘ + Enter）直接运行，键盘用户不必去点按钮；
 * - 通过 ref 暴露 `getValue / jumpToLine / focus`：运行取当前文本，traceback 点行号回到编辑器。
 *
 * 组件不读路由、不发请求、不写 localStorage——只吃 props 与 ref。
 */
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useImperativeHandle, useRef, forwardRef } from "react";

import type { RunnerLang } from "../protocol";

export interface CodeEditorHandle {
  /** 当前编辑器内容（运行按钮取它，不是取初始 props）。 */
  getValue: () => string;
  /** 光标移到第 N 行（1 起）并滚动到视野中央。 */
  jumpToLine: (line: number) => void;
  focus: () => void;
}

export interface CodeEditorProps {
  /** 初值；抽屉每次打开重新挂载，所以不做受控同步。 */
  value: string;
  lang: RunnerLang;
  ariaLabel: string;
  /** 代码被改动时回调（面板据此标记「已修改」）。 */
  onChange?: (value: string) => void;
  /** Ctrl/⌘ + Enter 运行。 */
  onRunShortcut?: () => void;
}

function languageExtension(lang: RunnerLang) {
  return lang === "python" ? python() : javascript();
}

export const CodeEditor = forwardRef<CodeEditorHandle, CodeEditorProps>(function CodeEditor(
  { value, lang, ariaLabel, onChange, onRunShortcut },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const onRunRef = useRef(onRunShortcut);

  onChangeRef.current = onChange;
  onRunRef.current = onRunShortcut;

  useEffect(() => {
    const parent = containerRef.current;
    if (!parent) return;

    const view = new EditorView({
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          EditorView.lineWrapping,
          EditorState.tabSize.of(4),
          languageExtension(lang),
          keymap.of([
            {
              key: "Mod-Enter",
              preventDefault: true,
              run: () => {
                onRunRef.current?.();
                return true;
              },
            },
          ]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) onChangeRef.current?.(update.state.doc.toString());
          }),
          EditorView.contentAttributes.of({ "aria-label": ariaLabel, "aria-multiline": "true" }),
        ],
      }),
      parent,
    });
    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // 只按语言重建：抽屉每次打开都是新挂载，`value` 作为初值足够
  }, [lang]); // eslint-disable-line react-hooks/exhaustive-deps

  useImperativeHandle(
    ref,
    () => ({
      getValue: () => viewRef.current?.state.doc.toString() ?? value,
      jumpToLine: (line: number) => {
        const view = viewRef.current;
        if (!view) return;
        const target = Math.min(Math.max(1, Math.trunc(line)), view.state.doc.lines);
        const info = view.state.doc.line(target);
        view.dispatch({
          selection: { anchor: info.from },
          effects: EditorView.scrollIntoView(info.from, { y: "center" }),
        });
        view.focus();
      },
      focus: () => viewRef.current?.focus(),
    }),
    [value],
  );

  return <div className="runner-editor" ref={containerRef} data-lang={lang} />;
});
