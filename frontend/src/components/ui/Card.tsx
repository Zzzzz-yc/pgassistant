/**
 * Card（需求文档 §9.4「容器与浮层」）。
 *
 * 五态：以 `hoverable` 开关
 * - hover：边框向 primary 靠 + 上浮 2px（§9.2 第 4 条允许的 hover 位移）
 * - focus-visible：可交互卡片（`interactive`）才有——非交互卡片的 focus 在内部元素上
 * - active：回到原位 + `--primary-soft` 底
 * - disabled：`aria-disabled` + `.ui-disabled`
 * - loading：**不适用**——Card 是布局容器；骨架态请用 `<Skeleton>` 组合出同尺寸占位
 */
import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "./cn";

export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  children: ReactNode;
  /** 悬浮反馈（整卡可点时用）。 */
  hoverable?: boolean;
  /** 内边距；`false` 时由子元素自己管（ResourceCard 的封面要贴边）。 */
  padded?: boolean;
  title?: ReactNode;
  /** 标题右侧的徽章/操作。 */
  headerExtra?: ReactNode;
  footer?: ReactNode;
  /** 整卡是一个交互目标（例如链接卡片）：加 `tabIndex` 与 focus ring。 */
  interactive?: boolean;
}

export function Card({
  children,
  hoverable = false,
  padded = true,
  title,
  headerExtra,
  footer,
  interactive = false,
  className,
  ...rest
}: CardProps) {
  return (
    <div
      {...rest}
      className={cn(
        "ui-card",
        padded && "ui-card--pad",
        hoverable && "ui-card--hoverable",
        className,
      )}
      tabIndex={interactive ? 0 : undefined}
    >
      {title || headerExtra ? (
        <div className="ui-card__header">
          {title ? <div className="ui-card__title">{title}</div> : null}
          {headerExtra ? <div className="ui-panel__actions">{headerExtra}</div> : null}
        </div>
      ) : null}
      {children}
      {footer ? <div className="ui-card__footer">{footer}</div> : null}
    </div>
  );
}
