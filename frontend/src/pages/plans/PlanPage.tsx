/**
 * 计划详情（时间轴） —— `/plans/:planId`
 *
 * Owner: 计划模块（M3）
 * 状态: **占位**，M3 替换。真实页面 = 粘性头（进度环）+ TimelineStage 时间轴 +
 *       阶段/条目的增删改（version 乐观锁）。
 * M0 只保证路由可到达。
 */
export default function PlanPage() {
  return (
    <section className="page page-enter">
      <h1 className="page__title">计划详情</h1>
      <p className="page__status">占位页：时间轴与条目编辑由 M3 计划模块实现。</p>
      <span className="page__owner">Owner: 计划模块（M3）</span>
    </section>
  );
}
