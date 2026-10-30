/**
 * copy-pyodide.mjs（Owner：代码运行 M6，需求文档 §20.2「Pyodide 为什么本地打包而不是走 CDN」）。
 *
 * 把 `node_modules/pyodide` 的运行时文件复制到 `public/assets/pyodide/`，由 Vite 作为静态资源
 * 原样发布。`npm run dev` / `npm run build` 之前由 `predev` / `prebuild` 自动执行。
 *
 * 为什么落在 `public/assets/` 而不是 `public/pyodide/`：
 * 生产态后端（backend/app/main.py:164）只挂载 `frontend/dist/assets` 这一个静态目录，其余
 * 路径一律走 SPA 回退成 `index.html`。放在 `public/pyodide/` 的话 `dist/pyodide/*` 在 dev 能
 * 取到、生产态却会拿到一段 HTML——`/assets/pyodide/*` 是唯一在 dev 与生产都可达的位置。
 * 后端属禁改范围，所以这里让位给既有挂载点。
 *
 * 该目录**不入库**（.gitignore），pyodide 的 13 MB 二进制只在本机生成；离线可用的前提是
 * 至少完成过一次 `npm install`（脚本据此复制）。
 */
import { createRequire } from "node:module";
import { cp, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, "..");
const TARGET = join(FRONTEND, "public", "assets", "pyodide");

/** 运行时真正会被 http 请求到的文件（其余 d.ts / map / console.html 不复制）。 */
const FILES = [
  "pyodide.js", // 经典 Worker 的加载器（worker.ts 用 eval 执行它，见该文件注释）
  "pyodide.mjs", // ESM 加载器（备用；不参与打包）
  "pyodide.asm.js", // Emscripten 胶水层（约 1.2 MB）
  "pyodide.asm.wasm", // CPython 本体（约 10 MB）
  "python_stdlib.zip", // 标准库（约 2.3 MB）
  "pyodide-lock.json", // 包索引（loadPyodide 必读）
];

/** 复制后校验体积的最低门槛：低于它就说明源包不完整。 */
const MIN_TOTAL_BYTES = 12 * 1024 * 1024;

function log(message) {
  process.stdout.write(`[copy-pyodide] ${message}\n`);
}

async function main() {
  let source;
  try {
    const require = createRequire(join(FRONTEND, "package.json"));
    source = dirname(require.resolve("pyodide/package.json"));
  } catch {
    log("跳过：没找到 node_modules/pyodide（先跑一次 npm install）。站内 Python 运行时不可用。");
    return;
  }

  let version = "unknown";
  try {
    version = JSON.parse(await readFile(join(source, "package.json"), "utf8")).version ?? "unknown";
  } catch {
    // 版本只用于日志与标记文件，读不到不影响复制
  }

  await mkdir(TARGET, { recursive: true });

  let copied = 0;
  let skipped = 0;
  let total = 0;

  for (const name of FILES) {
    const from = join(source, name);
    const to = join(TARGET, name);
    let sourceStat;
    try {
      sourceStat = await stat(from);
    } catch {
      log(`缺少源文件 ${name}（pyodide 包不完整），跳过`);
      continue;
    }
    total += sourceStat.size;

    let upToDate = false;
    try {
      const targetStat = await stat(to);
      upToDate = targetStat.size === sourceStat.size && targetStat.mtimeMs >= sourceStat.mtimeMs;
    } catch {
      upToDate = false;
    }

    if (upToDate) {
      skipped += 1;
      continue;
    }
    await cp(from, to);
    copied += 1;
  }

  if (total < MIN_TOTAL_BYTES) {
    log(`警告：本次复制总量只有 ${(total / 1024 / 1024).toFixed(1)} MB，源包可能不完整`);
  }

  // 记录一次版本号，便于排查「dev 页面里的运行时是旧版」这类问题
  await writeFile(
    join(TARGET, "VERSION.txt"),
    `pyodide ${version}\n从 node_modules/pyodide 复制于 predev/prebuild\n`,
    "utf8",
  );

  log(
    `完成：${copied} 个文件复制、${skipped} 个已是最新，共 ${(total / 1024 / 1024).toFixed(1)} MB → ` +
      "public/assets/pyodide（生产 URL 前缀 /assets/pyodide/）",
  );
}

await main();
