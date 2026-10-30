import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/**
 * 后端基址：生产态同源，开发态由 Vite 代理到本机 uvicorn。
 * 端口可能不是 8000（start.ps1 在 8000–8010 里挑），dev 时用 PG_BACKEND_ORIGIN 覆盖。
 */
const BACKEND_ORIGIN = (process.env.PG_BACKEND_ORIGIN ?? "").trim() || "http://127.0.0.1:8000";

/** 后端首页注入的令牌标签（backend/app/core/security.py 的 META_TAG）。 */
const TOKEN_META = /<meta\s+name=["']x-app-token["']\s+content=["']([^"']+)["']/i;

/**
 * 只在 dev 生效的令牌注入插件（§4.4 / §13.7 约束）。
 *
 * 令牌的唯一来源是后端 HTML 的 `<meta name="x-app-token">`，**进程内不缓存**：
 * 后端每个进程重新生成一枚令牌，一旦缓存，后端重启（或 `start.ps1 -Dev` 换端口重起）后
 * dev 页面会一直注入旧值，硬刷新也拿不到新值——这违背 API.md §1.2 第 5 条
 * 「TOKEN_INVALID → 提示重新加载页面，硬刷新重读 meta」的恢复承诺。
 *
 * 因此每次 `transformIndexHtml` 都重新抓一次：dev 下只有 index.html 走这条路
 * （assets 走静态管道），一次 127.0.0.1 的 GET 代价可忽略，换来「刷新即新令牌」。
 * 另外：
 * - 不写进 URL、不写进 Cookie、不写进 localStorage，进程内存里也不留副本；
 * - 后端没起来就注入空值（页面照常打开；接口会 403 TOKEN_MISSING，UI 提示重新加载），
 *   同一段失败只 `console.warn` 一次，恢复后再失败会重新提示，绝不让 dev server 崩。
 */
function devAppTokenPlugin(): Plugin {
  let warned = false;

  function warnOnce(logger: { warn(msg: string): void }, message: string): void {
    if (warned) return; // 每次请求都刷屏没有意义，失败期间只提示一次
    warned = true;
    logger.warn(`[pgassistant] ${message}`);
  }

  async function readToken(logger: { warn(msg: string): void }): Promise<string> {
    try {
      const res = await fetch(`${BACKEND_ORIGIN}/`, {
        headers: { accept: "text/html", "cache-control": "no-cache" },
      });
      const token = TOKEN_META.exec(await res.text())?.[1] ?? "";
      if (token) {
        warned = false; // 恢复了：下次再失败要重新提示
        return token;
      }
      warnOnce(
        logger,
        `${BACKEND_ORIGIN}/ 里还没有 x-app-token meta：后端可能没起好。` +
          "接口会返回 403 TOKEN_MISSING；后端起来后刷新页面即可（每次刷新都会重新取令牌）。",
      );
      return "";
    } catch (cause) {
      warnOnce(
        logger,
        `连不上后端 ${BACKEND_ORIGIN}（${String(cause)}）：先跑 start.ps1（或 uvicorn）再刷新本页；` +
          "否则 /api 代理与令牌注入都不可用。",
      );
      return "";
    }
  }

  return {
    name: "pgassistant-dev-app-token",
    apply: "serve",
    async configureServer(server) {
      // 启动时探一次，只为在终端早一点给出「连没连上后端」的信号（不缓存结果）
      const token = await readToken(server.config.logger);
      if (token) {
        server.config.logger.info(
          `[pgassistant] 已从 ${BACKEND_ORIGIN}/ 取到令牌（${token.length} 字符）；` +
            "每次刷新页面都会重新取，后端重启后无需重启 Vite",
        );
      }
    },
    async transformIndexHtml(html) {
      const token = await readToken({ warn: (msg) => console.warn(msg) });
      if (!token) return html; // 取不到就不注入；页面照常打开，出错由 UI 提示
      // 生产构建走的是后端中间件注入，这里只在 dev server 里补 meta
      if (TOKEN_META.test(html)) return html;
      return html.replace("</head>", `    <meta name="x-app-token" content="${token}" />\n  </head>`);
    },
  };
}

export default defineConfig({
  plugins: [react(), devAppTokenPlugin()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // 固定 127.0.0.1 与 5173：PG_DEV_ORIGINS 的白名单是精确匹配的
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      // 同源代理：浏览器看到的 Origin 是 :5173，后端靠 PG_DEV_ORIGINS 放行写操作
      "/api": { target: BACKEND_ORIGIN, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
