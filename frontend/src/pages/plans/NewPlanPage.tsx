/**
 * 生成计划 —— `/plans/new`
 *
 * Owner: 计划模块（M3）
 * 状态: **占位**，M3 替换。真实页面 = 一句话 → 表单 → 草稿预览 → 保存，
 *       依赖 listPlanTemplates / getPlanTemplate / extractSlots / planDraft / createPlan。
 * M0 只保证路由可到达（注意 `/plans/new` 必须先于 `/plans/:planId` 命中）。
 */
export default function NewPlanPage() {
  return (
    <section className="page page-enter">
      <h1 className="page__title">生成计划</h1>
      <p className="page__status">占位页：表单、模板与草稿预览由 M3 计划模块实现。</p>
      <span className="page__owner">Owner: 计划模块（M3）</span>
    </section>
  );
}
