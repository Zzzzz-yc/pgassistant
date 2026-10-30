/**
 * 首页 —— `/`
 *
 * Owner: 资料页（M2）
 * 状态: **占位**，M2 替换。真实首页 = 大搜索框 + 三个方向入口 + 「继续学习」3 条，
 *       依赖 getHealth / getTaxonomy / listPlans({status:"active"}) / getPlan(activeId)。
 * M0 只保证路由可到达、壳与样式基座可用。
 */
import { useTranslation } from "react-i18next";

export default function HomePage() {
  const { t } = useTranslation("nav");
  return (
    <section className="page page-enter">
      <h1 className="page__title">{t("home")}</h1>
      <p className="page__status">占位页：搜索入口与「继续学习」区块由 M2 资料页实现。</p>
      <span className="page__owner">Owner: 资料页（M2）</span>
    </section>
  );
}
