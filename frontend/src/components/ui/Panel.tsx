/**
 * Panel（需求文档 §9.4「容器与浮层」）：带标题栏/操作区/底栏的分组容器。
 *
 * 五态：hover / focus-visible / active / disabled / loading **不适用**——Panel 是布局容器，
 * 交互全在它内部的按钮上；骨架态用 `<Skeleton>` 组合。
 */
import type { ReactNode } from "react";

import { cn } from "./cn";

export interface PanelProps {
  title?: ReactNode;
  description?: ReactNode;
  /** 标题右侧操作（按钮组）。 */
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  /** 正文去掉内边距（列表、表格贴边时用）。 */
  flush?: boolean;
  className?: string;
  bodyClassName?: string;
}

export function Panel({
  title,
  description,
  actions,
  footer,
  children,
  flush = false,
  className,
  bodyClassName,
}: PanelProps) {
  return (
    <section className={cn("ui-panel", className)}>
      {title || description || actions ? (
        <header className="ui-panel__header">
          <div className="ui-panel__heading">
            {title ? <h2 className="ui-panel__title">{title}</h2> : null}
            {description ? <p className="ui-panel__desc">{description}</p> : null}
          </div>
          {actions ? <div className="ui-panel__actions">{actions}</div> : null}
        </header>
      ) : null}

      <div className={cn("ui-panel__body", flush && "ui-panel__body--flush", bodyClassName)}>
        {children}
      </div>

      {footer ? <footer className="ui-panel__footer">{footer}</footer> : null}
    </section>
  );
}
