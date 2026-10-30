/**
 * 助手对话 —— `/assistant`
 *
 * Owner: 助手模块（M4）
 * 状态: **占位**，M4 替换。真实页面 = 消息列表 + SSE 流式回复 + 意图标签 + 能力边界提示条。
 * M0 只保证路由可到达；传输层的 SSE 读取已就绪（`lib/http.ts::readSSE`）。
 */
import { useTranslation } from "react-i18next";

export default function AssistantPage() {
  const { t } = useTranslation("nav");
  return (
    <section className="page page-enter">
      <h1 className="page__title">{t("assistant")}</h1>
      <p className="page__status">占位页：对话与流式渲染由 M4 助手模块实现。</p>
      <span className="page__owner">Owner: 助手模块（M4）</span>
    </section>
  );
}
