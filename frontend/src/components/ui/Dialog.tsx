/**
 * Dialog（需求文档 §9.4「容器与浮层」+ §9.7「弹层打开时焦点锁定，Esc 关闭，
 * 关闭后焦点回到触发元素」）。
 *
 * 焦点契约：
 * - **锁定**、Esc 关闭、遮罩点击关闭由 Radix Dialog 提供；
 * - **关闭后焦点归还**由本文件显式做（`useReturnFocus`）：本组件是受控 `open`、不挂
 *   Trigger，Radix 没有可归还的目标，实测会停在 `BODY`。做法是打开时记录
 *   `document.activeElement`，关闭时 `preventDefault()` 掉 Radix 的默认归还再自己 focus 回去。
 *
 * 另外保证：关闭键有 `aria-label`（默认「关闭」）；标题用 Radix 的 Title 关联；正文可滚动。
 *
 * 五态（落在关闭按钮上，走 IconButton）：hover / focus-visible / active / disabled /
 * loading 五态齐全；Dialog 容器本身没有 hover/active 语义。
 */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { IconButton } from "./IconButton";
import { useReturnFocus } from "./focus";

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** 无 title 时的可访问名。 */
  ariaLabel?: string;
  closeLabel?: string;
  /** 内容宽度（px）；看图片的灯箱可传更大值。 */
  width?: number | string;
  /** 隐藏右上角关闭键（例如必须二选一的确认框）。 */
  hideClose?: boolean;
  /** 追加到内容容器的类名（特殊版式，例如命令面板贴顶部时改定位）。 */
  className?: string;
}

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  ariaLabel,
  closeLabel = "关闭",
  width,
  hideClose = false,
  className,
}: DialogProps) {
  const focus = useReturnFocus();

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="ui-overlay" />
        <DialogPrimitive.Content
          className={className ? `ui-dialog ${className}` : "ui-dialog"}
          aria-label={title ? undefined : ariaLabel}
          aria-describedby={undefined}
          onOpenAutoFocus={focus.onOpenAutoFocus}
          onCloseAutoFocus={focus.onCloseAutoFocus}
          style={width ? { width: `min(${typeof width === "number" ? `${width}px` : width}, calc(100vw - var(--space-8)))` } : undefined}
        >
          {title || description || !hideClose ? (
            <div className="ui-dialog__head">
              <div className="ui-dialog__heading">
                {title ? (
                  <DialogPrimitive.Title className="ui-dialog__title">{title}</DialogPrimitive.Title>
                ) : null}
                {description ? (
                  <DialogPrimitive.Description className="ui-dialog__desc">
                    {description}
                  </DialogPrimitive.Description>
                ) : null}
              </div>
              {hideClose ? null : (
                <DialogPrimitive.Close asChild>
                  <IconButton label={closeLabel} icon={<X size={16} />} size="sm" variant="ghost" />
                </DialogPrimitive.Close>
              )}
            </div>
          ) : null}

          <div className="ui-dialog__body">{children}</div>
          {footer ? <div className="ui-dialog__footer">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
