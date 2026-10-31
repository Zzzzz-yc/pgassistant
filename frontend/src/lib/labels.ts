/**
 * 分类 / 技术栈本地化名的取值（D28 / `API.md` §3.2）。
 *
 * 规则（逐条对应 D28 裁定第 3、4 条）：
 * - 语言按**当前界面语言**取：`zh-CN` → `zh`，`en-US` → `en`；不得硬编码某一种语言；
 * - `label` 为 `null` / 缺席 / 当前语言取不到时返回 `null`，调用方**必须**回退
 *   `category`（或 `stack`）原值，不得留空、不得自行翻译；
 * - 这里不拼 slug、不做任何内置映射：显示名只来自接口的 `label`。
 */
import type { LocalizedLabel } from "@/types/api";

export type LabelLanguage = "zh" | "en";

/** 界面 locale → 标签语言。未知值按默认语言 `zh`（`i18n/index.ts` 的 `DEFAULT_LOCALE`）。 */
export function labelLanguage(locale: string | null | undefined): LabelLanguage {
  return locale?.toLowerCase().startsWith("en") ? "en" : "zh";
}

/** 取当前语言下的显示名；拿不到就返回 `null`，由调用方回退原始 key。 */
export function localizedLabel(
  label: LocalizedLabel | null | undefined,
  locale: string | null | undefined,
): string | null {
  if (!label) return null;
  const text = label[labelLanguage(locale)];
  return typeof text === "string" && text.trim() ? text : null;
}
