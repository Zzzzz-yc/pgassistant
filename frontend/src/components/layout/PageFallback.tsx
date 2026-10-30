/**
 * 页面级加载态（Owner：前端骨架 M0）。
 *
 * 这是 `React.lazy` + `Suspense` 的兜底，**不是** `components/ui/**` 的正式组件：
 * Skeleton / Spinner / EmptyState 的完整五态由组件库 M2 落地，届时各页面换成它们即可。
 * 这里坚持「骨架与真实布局同尺寸」的原则（§9.6：不能跳动）。
 */
export interface PageFallbackProps {
  /** 屏幕阅读器可读的一句话，默认「加载中…」。 */
  label?: string;
  rows?: number;
}

export function PageFallback({ label = "加载中…", rows = 3 }: PageFallbackProps) {
  return (
    <div className="page-fallback" role="status" aria-live="polite">
      <span className="visually-hidden">{label}</span>
      <div className="page-fallback__row">
        <span className="spinner" aria-hidden="true" />
        <span>{label}</span>
      </div>
      {Array.from({ length: rows }, (_, index) => (
        <span
          key={index}
          className="skeleton"
          aria-hidden="true"
          style={{ width: index === rows - 1 ? "60%" : "100%" }}
        />
      ))}
    </div>
  );
}
