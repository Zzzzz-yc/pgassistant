/**
 * CommandPalette 挂载点（Owner：前端骨架 M0）。
 *
 * **只做三件事**：Ctrl/⌘+K 打开、Esc 关闭、焦点进出管理（§9.7）。
 * 真正的命令面板（搜索框、结果分组、键盘上下选择）由组件库 M2 的
 * `components/business/CommandPalette.tsx` 实现，接进这里替换 `palette__body`。
 */
import { Search } from "lucide-react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const { t } = useTranslation(["common", "nav"]);
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panelRef.current?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      restoreFocusRef.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="palette-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label={t("nav:openSearch")}
        tabIndex={-1}
      >
        <div className="palette__head">
          <Search size={15} aria-hidden="true" />
          <span>{t("palette.placeholder")}</span>
          <span style={{ marginLeft: "auto" }}>{t("palette.hint")}</span>
        </div>
        <div className="palette__body">{t("palette.empty")}</div>
      </div>
    </div>,
    document.body,
  );
}
