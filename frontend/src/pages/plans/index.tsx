/**
 * 计划列表 —— `/plans`
 *
 * Owner: 计划模块（M3）
 * 状态: **占位**，M3 替换。真实页面 = 计划卡片 + 进度摘要 + 归档/激活。
 * M0 只保证路由可到达。
 */
import { useTranslation } from "react-i18next";

export default function PlansPage() {
  const { t } = useTranslation("nav");
  return (
    <section className="page page-enter">
      <h1 className="page__title">{t("plans")}</h1>
      <p className="page__status">占位页：计划列表与归档操作由 M3 计划模块实现。</p>
      <span className="page__owner">Owner: 计划模块（M3）</span>
    </section>
  );
}
