/**
 * Pyodide 运行时的本地缓存（需求文档 §20.2「本地打包，加载后缓存到 CacheStorage」、
 * §20.6「运行时未加载且离线 → 运行时未就绪，请联网完成首次加载 + 重试按钮」）。
 *
 * 两层缓存各管一段：
 * 1. **CacheStorage**（这里）：显式把 5 个运行时文件写进 `pgassistant-pyodide-v1`，
 *    Worker 内的 fetch 会被 `worker.ts` 的拦截器优先读这份缓存——离线也能起来，且「是否
 *    缓存过」是可查询的事实，而不是猜浏览器的 HTTP 缓存。
 * 2. **浏览器 HTTP 缓存**：CacheStorage 未命中时的兜底，不依赖。
 *
 * 资源来自 `public/assets/pyodide/`（不是 CDN，见前端 README 与本目录注释）。
 */

/** 运行时资源前缀。放在 `/assets/` 下是因为生产态后端只挂载 `dist/assets`（见 copy-pyodide.mjs）。 */
export const PYODIDE_BASE = "/assets/pyodide/";

/** 缓存桶名字；运行时升级时改版本号即可让旧缓存自然淘汰。 */
export const PYODIDE_CACHE_NAME = "pgassistant-pyodide-v1";

/** 判断「是否已缓存」与预热都要覆盖的文件（`loadPyodide` 实际会请求的全部 http 资源）。 */
export const PYODIDE_ASSETS: readonly string[] = [
  "pyodide.js",
  "pyodide.asm.js",
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
];

/** 约 13.2 MB，给「正在准备运行时」的文案用（不参与判断）。 */
export const PYODIDE_APPROX_BYTES = 13.2 * 1024 * 1024;

function cacheAvailable(): boolean {
  return typeof caches !== "undefined";
}

/**
 * 运行时是否完整落在 CacheStorage 里。
 * 拿不到 CacheStorage（浏览器禁用 / 非安全上下文）时返回 `false`——当作「未缓存」处理，
 * 由调用方决定是提示还是直接尝试联网加载。
 */
export async function isPyodideCached(): Promise<boolean> {
  if (!cacheAvailable()) return false;
  try {
    const cache = await caches.open(PYODIDE_CACHE_NAME);
    for (const name of PYODIDE_ASSETS) {
      const hit = await cache.match(PYODIDE_BASE + name);
      if (!hit) return false;
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * 把运行时文件下载并写入 CacheStorage（首次运行时做一次）。
 *
 * 返回 `true` = 全部就绪。任一步失败都返回 `false`（离线、断网、后端没挂到该路径），
 * 由调用方给出「运行时未就绪，请联网完成首次加载」+ 重试。
 */
export async function ensurePyodideCached(
  onProgress?: (done: number, total: number) => void,
): Promise<boolean> {
  if (!cacheAvailable()) return false;
  const total = PYODIDE_ASSETS.length;
  let done = 0;
  onProgress?.(0, total);

  let cache: Cache;
  try {
    cache = await caches.open(PYODIDE_CACHE_NAME);
  } catch {
    return false;
  }

  for (const name of PYODIDE_ASSETS) {
    const url = PYODIDE_BASE + name;
    try {
      const existing = await cache.match(url);
      if (!existing) {
        const response = await fetch(url, { credentials: "same-origin" });
        if (!response.ok) return false;
        // 逐字节读完再 put：13 MB 的 wasm 走流式 put 在部分浏览器上会留下半截缓存
        const body = await response.arrayBuffer();
        await cache.put(
          url,
          new Response(body, {
            status: 200,
            headers: {
              "content-type": response.headers.get("content-type") ?? "application/octet-stream",
              "content-length": String(body.byteLength),
            },
          }),
        );
      }
      done += 1;
      onProgress?.(done, total);
    } catch {
      return false;
    }
  }
  return true;
}

/** 清掉运行时缓存（设置页/排障用；本模块不提供 UI，只在报告里登记入口）。 */
export async function clearPyodideCache(): Promise<boolean> {
  if (!cacheAvailable()) return false;
  try {
    return await caches.delete(PYODIDE_CACHE_NAME);
  } catch {
    return false;
  }
}
