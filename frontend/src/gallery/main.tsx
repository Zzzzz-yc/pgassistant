/**
 * 组件画廊入口（dev only，`/gallery.html`）。
 *
 * 刻意不引 `styles/layout.css`：画廊只展示组件，不展示 AppShell；
 * 需要"页面级底色"的地方用 `gallery.css` 自己的 `.g-page`。
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@/styles/tokens.css";
import "@/styles/global.css";
import "@/i18n";

import { Gallery } from "./Gallery";
import "./gallery.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("找不到 #root 挂载点：gallery.html 被改坏了？");
}

createRoot(container).render(
  <StrictMode>
    <Gallery />
  </StrictMode>,
);
