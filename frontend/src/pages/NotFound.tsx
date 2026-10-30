/**
 * 404 —— `*`
 *
 * Owner: 前端骨架（M0）
 * 状态: 完成后保留（这是 M0 自己的页面，不是占位）。文案走 i18n，动作回首页。
 */
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

export default function NotFoundPage() {
  const { t } = useTranslation(["common", "errors"]);
  return (
    <section className="page page-enter">
      <h1 className="page__title">404</h1>
      <p className="page__status">{t("errors:NOT_FOUND.message")}</p>
      <p style={{ marginTop: "var(--space-4)" }}>
        <Link className="button" to="/">
          {t("common:action.backHome")}
        </Link>
      </p>
      <span className="page__owner">Owner: 前端骨架（M0）</span>
    </section>
  );
}
