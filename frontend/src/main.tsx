/**
 * 入口（Owner：前端骨架 M0）。
 * 顺序有讲究：tokens → global（含 Tailwind 指令）→ layout，后写的覆盖先写的。
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@/styles/tokens.css";
import "@/styles/global.css";
import "@/styles/layout.css";
import "@/i18n";

import App from "@/App";

const container = document.getElementById("root");
if (!container) {
  throw new Error("找不到 #root 挂载点：index.html 被改坏了？");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
