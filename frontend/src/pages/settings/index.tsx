/**
 * 设置 —— `/settings`
 *
 * Owner: 打磨模块（M5）
 * 状态: **占位**，M5 替换。真实页面 = 四个 Section（模型 / 语义检索 / 私有资料 / 通用）。
 * M0 只保证路由可到达。
 */
import { useTranslation } from "react-i18next";

export default function SettingsPage() {
  const { t } = useTranslation("nav");
  return (
    <section className="page page-enter">
      <h1 className="page__title">{t("settings")}</h1>
      <p className="page__status">占位页：模型与索引设置由 M5 打磨模块实现。</p>
      <span className="page__owner">Owner: 打磨模块（M5）</span>
    </section>
  );
}
