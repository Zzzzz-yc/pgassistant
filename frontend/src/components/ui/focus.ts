/**
 * 弹层关闭后把焦点还给「打开它的那个元素」（需求文档 §9.7 第 4 条）。
 *
 * 为什么不能只依赖 Radix：Radix Dialog 关掉时会把焦点还给它的 `<Dialog.Trigger>`。
 * 本项目的 Dialog / Sheet **都是受控 `open`、不挂 Trigger** 的用法（命令面板由 Ctrl/⌘+K
 * 或顶栏搜索按钮驱动），没有 Trigger 可归还 —— 实测关闭后 `document.activeElement`
 * 停在 `BODY`，所以这一条必须自己做。
 *
 * 交接方式（两个 handler 挂到 Radix Content 上）：
 * - `onOpenAutoFocus`：此刻 Radix 还没把焦点移进弹层，记录下来的就是"触发元素"；
 *   不 `preventDefault()`，焦点锁定仍由 Radix 负责。
 * - `onCloseAutoFocus`：**先 `preventDefault()`** 掉 Radix 的默认归还，再由这里显式
 *   `focus()` 回去；否则会出现"Radix 还一次、我们再还一次"的双焦点抖动。
 *
 * 元素已被移出 DOM 时回退到 `body`（临时给它 `tabindex="-1"` 才可聚焦，聚焦后把属性
 * 还原），保证焦点不会丢在"无处可去"的状态。
 */
import { useCallback, useRef } from "react";

export interface AutoFocusEvent {
  preventDefault: () => void;
}

export interface ReturnFocusHandlers {
  onOpenAutoFocus: (event: AutoFocusEvent) => void;
  onCloseAutoFocus: (event: AutoFocusEvent) => void;
}

/** 把焦点放到 `target`；不可用则退到 body。 */
export function restoreFocus(target: HTMLElement | null): void {
  if (target && target.isConnected) {
    target.focus({ preventScroll: true });
    if (document.activeElement === target) return;
  }

  const body = document.body;
  const previous = body.getAttribute("tabindex");
  body.setAttribute("tabindex", "-1");
  body.focus({ preventScroll: true });
  if (previous === null) body.removeAttribute("tabindex");
  else body.setAttribute("tabindex", previous);
}

/**
 * 返回一对 Radix Content 的焦点事件处理函数。每个弹层实例各自持有一份（用 ref 记录，
 * 不是全局变量），嵌套弹层时不会互相覆盖。
 */
export function useReturnFocus(): ReturnFocusHandlers {
  const targetRef = useRef<HTMLElement | null>(null);

  const onOpenAutoFocus = useCallback(() => {
    const active = typeof document === "undefined" ? null : document.activeElement;
    targetRef.current = active instanceof HTMLElement ? active : null;
  }, []);

  const onCloseAutoFocus = useCallback((event: AutoFocusEvent) => {
    event.preventDefault();
    restoreFocus(targetRef.current);
    targetRef.current = null;
  }, []);

  return { onOpenAutoFocus, onCloseAutoFocus };
}
