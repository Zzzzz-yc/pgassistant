/**
 * 侧栏内看资料 —— `/plans/:planId/items/:slug(*)`
 *
 * Owner: 计划模块（M3）+ 资料页组件（M2）
 * 状态: **占位**，M3 替换。真实页面 = 左侧计划树不动、右侧内容区加载该资料（§9.1，不跳页）。
 * M0 只保证 splat 路由能同时容纳 planId 与含 `/` 的 slug。
 */
export default function PlanItemPage() {
  return (
    <section className="page page-enter">
      <h1 className="page__title">计划内资料</h1>
      <p className="page__status">占位页：计划树内联阅读由 M3 计划模块实现。</p>
      <span className="page__owner">Owner: 计划模块（M3）+ 资料页（M2）</span>
    </section>
  );
}
