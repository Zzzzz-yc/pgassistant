/**
 * 资料库 —— `/library`
 *
 * Owner: 资料页（M2）
 * 状态: **占位**，M2 替换。真实页面 = FilterBar + ResourceCard 网格 + 分页 +
 *       「共 N 篇 · 用时 xms」，筛选状态全部同步到 URL query。
 * M0 只保证路由可到达。
 */
import { useTranslation } from "react-i18next";

export default function LibraryPage() {
  const { t } = useTranslation("nav");
  return (
    <section className="page page-enter">
      <h1 className="page__title">{t("library")}</h1>
      <p className="page__status">占位页：筛选、网格与分页由 M2 资料页实现。</p>
      <span className="page__owner">Owner: 资料页（M2）</span>
    </section>
  );
}
