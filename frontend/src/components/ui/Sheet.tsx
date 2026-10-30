/**
 * Sheet（需求文档 §9.4「容器与浮层」：移动端抽屉；§20.4 移动端运行面板也是它）。
 *
 * 与 Dialog 同一套焦点契约（都用 Radix Dialog 原语，只是版式改成侧贴）：锁定 / Esc 由
 * Radix 提供，**关闭后焦点归还**用同一份 `useReturnFocus`（本组件也是受控 `open`、
 * 不挂 Trigger，不能指望 Radix 自己还）。
 *
 * 桌面端由调用方决定要不要开（`<768` 手机断点，§9.8），组件本身不读窗口宽度——
 * 组件只吃 props、不读路由/不断点，是刻意的边界。
 *
 * 五态：同 Dialog（容器无 hover/active；关闭键走 IconButton 的五态）。
 */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { IconButton } from "./IconButton";
import { useReturnFocus } from "./focus";

export type SheetSide = "right" | "left" | "bottom";

export interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  side?: SheetSide;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  ariaLabel?: string;
  closeLabel?: string;
}

export function Sheet({
  open,
  onOpenChange,
  side = "right",
  title,
  children,
  footer,
  ariaLabel,
  closeLabel = "关闭",
}: SheetProps) {
  const focus = useReturnFocus();

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="ui-overlay" />
        <DialogPrimitive.Content
          className={`ui-sheet ui-sheet--${side}`}
          aria-label={title ? undefined : ariaLabel}
          onOpenAutoFocus={focus.onOpenAutoFocus}
          onCloseAutoFocus={focus.onCloseAutoFocus}
          aria-describedby={undefined}
        >
          <div className="ui-sheet__head">
            {title ? (
              <DialogPrimitive.Title className="ui-sheet__title">{title}</DialogPrimitive.Title>
            ) : null}
            <DialogPrimitive.Close asChild>
              <IconButton label={closeLabel} icon={<X size={16} />} size="sm" variant="ghost" />
            </DialogPrimitive.Close>
          </div>
          <div className="ui-sheet__body">{children}</div>
          {footer ? <div className="ui-dialog__footer">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
