/**
 * i18n 初始化（Owner：前端骨架 M0，MODULES.md §1.2）。
 *
 * 约定：
 * - 命名空间固定三个：`common` / `nav` / `errors`；各模块只往自己的 namespace 加键，
 *   不新建 namespace（要新建先找前端骨架）。
 * - 语言持久化到 `localStorage["pg_lang"]`（**只有语言**；令牌绝不进存储）。
 * - 错误码 → 文案**只在这里**（`locales/<lang>/errors.json`），页面不得写 if 链。
 */
import i18n, { type Resource } from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";

import type { Locale } from "@/types/api";

import enCommon from "./locales/en-US/common.json";
import enErrors from "./locales/en-US/errors.json";
import enNav from "./locales/en-US/nav.json";
import zhCommon from "./locales/zh-CN/common.json";
import zhErrors from "./locales/zh-CN/errors.json";
import zhNav from "./locales/zh-CN/nav.json";

export const LANG_STORAGE_KEY = "pg_lang";
export const DEFAULT_LOCALE: Locale = "zh-CN";
export const SUPPORTED_LOCALES: readonly Locale[] = ["zh-CN", "en-US"];
export const NAMESPACES = ["common", "nav", "errors"] as const;
export type Namespace = (typeof NAMESPACES)[number];

const resources: Resource = {
  "zh-CN": { common: zhCommon, nav: zhNav, errors: zhErrors },
  "en-US": { common: enCommon, nav: enNav, errors: enErrors },
};

const KNOWN_ERROR_CODES = new Set(Object.keys(zhErrors));

function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

/** 启动时读 localStorage；非法值或不支持的语言一律回退 zh-CN。 */
export function resolveInitialLocale(): Locale {
  try {
    const saved = window.localStorage.getItem(LANG_STORAGE_KEY);
    if (isLocale(saved)) return saved;
  } catch {
    // 隐私模式读不到 localStorage：按默认语言渲染，不影响功能
  }
  return DEFAULT_LOCALE;
}

/** 切语言：立即生效并持久化（这是唯一允许写 localStorage 的业务键）。 */
export function setLocale(locale: Locale): void {
  void i18n.changeLanguage(locale);
  try {
    window.localStorage.setItem(LANG_STORAGE_KEY, locale);
  } catch {
    // 存不下也不影响本次会话
  }
}

/** 当前语言（规范化到受支持的两种之一）。 */
export function currentLocale(): Locale {
  return isLocale(i18n.resolvedLanguage) ? i18n.resolvedLanguage : DEFAULT_LOCALE;
}

/** 错误码是否有专门文案（未知码走 UNKNOWN，§9.9）。 */
export function isKnownErrorCode(code: string | null | undefined): boolean {
  return !!code && KNOWN_ERROR_CODES.has(code);
}

/** 错误码 → 一句话文案。未知码回退 UNKNOWN。 */
export function errorMessage(code: string | null | undefined): string {
  const key = isKnownErrorCode(code) ? (code as string) : "UNKNOWN";
  return i18n.t(`${key}.message`, { ns: "errors" });
}

/** 错误码 → 建议动作（§9.9 的“动作”列）。 */
export function errorAction(code: string | null | undefined): string {
  const key = isKnownErrorCode(code) ? (code as string) : "UNKNOWN";
  return i18n.t(`${key}.action`, { ns: "errors" });
}

/** 组件里用这个版本，语言切换时会重新渲染。 */
export function useErrorMessage(): (code: string | null | undefined) => string {
  const { t, i18n: instance } = useTranslation("errors");
  return (code: string | null | undefined) => {
    const key = isKnownErrorCode(code) ? (code as string) : "UNKNOWN";
    // 依赖 i18n.language 让 React 在下一次 changeLanguage 后重新求值
    void instance.language;
    return t(`${key}.message`);
  };
}

void i18n.use(initReactI18next).init({
  resources,
  lng: resolveInitialLocale(),
  fallbackLng: DEFAULT_LOCALE,
  supportedLngs: [...SUPPORTED_LOCALES],
  ns: [...NAMESPACES],
  defaultNS: "common",
  interpolation: { escapeValue: false },
  react: { useSuspense: false },
});

/** <html lang> 与语言保持同步（读屏软件依赖它）。 */
i18n.on("languageChanged", (locale) => {
  if (typeof document !== "undefined") {
    document.documentElement.lang = isLocale(locale) ? locale : DEFAULT_LOCALE;
  }
});

export default i18n;
