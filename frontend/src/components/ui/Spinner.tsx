/**
 * Spinner（需求文档 §9.4「基础」）。
 *
 * 五态：hover / focus-visible / active / disabled **不适用**——Spinner 是纯装饰的
 * 加载指示，不接收指针事件、不可聚焦；它自身就是其他组件的 loading 态表达。
 * 无障碍：视觉上是转圈，读屏靠 `label` 的 sr-only 文本（默认「加载中」）。
 */
import { cn } from "./cn";

export type SpinnerSize = "sm" | "md" | "lg";

export interface SpinnerProps {
  size?: SpinnerSize;
  /** 读屏文案；组件内已经 sr-only，不会占版面。 */
  label?: string;
  className?: string;
}

export function Spinner({ size = "md", label = "加载中", className }: SpinnerProps) {
  return (
    <span role="status" className={cn("ui-inline-icon", className)}>
      <span
        className={cn("ui-spinner", size !== "md" && `ui-spinner--${size}`)}
        aria-hidden="true"
      />
      <span className="ui-loading-label">{label}</span>
    </span>
  );
}
