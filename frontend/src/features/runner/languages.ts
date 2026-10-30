/**
 * 语言映射（需求文档 §20.1 / §20.10）。
 *
 * 判定只看 front-matter 的 `runnable` 字段，**不靠目录名推断**；语言别名归一化后再比对，
 * 因为正文代码围栏可能写 `js` / `py` 而 front-matter 写的是全称。
 */
import type { RunnerLang } from "./protocol";

/** 站内真正能跑的语言（§20.2：Python = Pyodide，JavaScript = 浏览器原生）。 */
export const RUNNER_LANGS: readonly RunnerLang[] = ["python", "javascript"];

/** 代码围栏语言 → 站内语言；不可运行的语言返回 `null`。 */
const ALIASES: Record<string, RunnerLang> = {
  python: "python",
  python3: "python",
  py: "python",
  javascript: "javascript",
  js: "javascript",
  mjs: "javascript",
  node: "javascript",
};

/** 徽章上的显示名。 */
const LABELS: Record<RunnerLang, string> = {
  python: "Python",
  javascript: "JavaScript",
};

export function normalizeLang(lang: string | null | undefined): string {
  return (lang ?? "").trim().toLowerCase();
}

/** 归一化后的代码围栏语言 → 站内语言（不支持则 `null`）。 */
export function runnerLangOf(lang: string | null | undefined): RunnerLang | null {
  return ALIASES[normalizeLang(lang)] ?? null;
}

export function langLabel(lang: RunnerLang): string {
  return LABELS[lang];
}

/**
 * 这篇资料的 `runnable` 是否允许该语言运行（§20.1「只有列表内的语言显示运行按钮」）。
 * `runnable` 为空 / `null` 时一律不允许——没标记的资料不出现运行入口。
 */
export function isRunnableLang(
  lang: string | null | undefined,
  runnable: readonly string[] | null | undefined,
): boolean {
  const target = runnerLangOf(lang);
  if (!target || !runnable || runnable.length === 0) return false;
  return runnable.some((item) => runnerLangOf(item) === target);
}

/** 该语言是否属于「浏览器里跑不起来」的那一类（给禁用态 + Tooltip 用）。 */
export function isUnsupportedLang(lang: string | null | undefined): boolean {
  return runnerLangOf(lang) === null;
}

/**
 * 代码围栏写的是不是**一门编程语言**。
 *
 * `text` / `src` / `shell` / `markdown` 这些不是语言，而是排版块（hello-algo 的 `text title="<6>"`
 * 全是图示、`src` 是结构注释）。给它们挂一个禁用的「运行」按钮只会制造噪音，所以直接不渲染；
 * 只有 C++ / Java / Go / Rust 这类**真语言**才按 §20.10 显示禁用态 + Tooltip。
 */
const PROGRAMMING_LANGUAGES = new Set([
  "python",
  "python3",
  "py",
  "javascript",
  "js",
  "mjs",
  "node",
  "typescript",
  "ts",
  "c",
  "cpp",
  "c++",
  "cc",
  "csharp",
  "cs",
  "java",
  "kotlin",
  "go",
  "golang",
  "rust",
  "rs",
  "swift",
  "dart",
  "ruby",
  "rb",
  "php",
  "scala",
  "haskell",
  "lua",
  "perl",
  "r",
  "matlab",
  "objectivec",
  "objc",
  "sql",
  "zig",
  "elixir",
  "clojure",
  "julia",
]);

export function isProgrammingLanguage(lang: string | null | undefined): boolean {
  return PROGRAMMING_LANGUAGES.has(normalizeLang(lang));
}
