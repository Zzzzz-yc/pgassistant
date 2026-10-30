/**
 * 资料详情 —— `/library/:slug(*)`
 *
 * Owner: 资料页（M2）
 * 状态: **占位**，M2 替换。真实页面 = 正文（68ch）+ 右侧粘性目录 + 上下节 +
 *       相关推荐 + 「加入计划」，依赖 getResource / getRelated / addPlanItem。
 * M0 只保证 splat 路由能命中（slug 含 `/`，D2）。
 */
export default function ResourcePage() {
  return (
    <section className="page page-enter">
      <h1 className="page__title">资料详情</h1>
      <p className="page__status">占位页：正文渲染与目录由 M2 资料页实现。</p>
      <span className="page__owner">Owner: 资料页（M2）</span>
    </section>
  );
}
