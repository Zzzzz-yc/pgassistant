/**
 * RunButton（需求文档 §9.4「RunButton：代码块右上角，仅 runnable 语言显示；非支持语言禁用并
 * Tooltip 说明」、§20.1、§20.10）。
 *
 * 三个开关依次判定，任一不满足就**不渲染**或渲染禁用态：
 * 1. 全局设置 `runner_enabled` 关闭 → 不渲染（入口消失，§20.5-7）；
 * 2. 浏览器不支持 Worker / WASM → 不渲染（§20.6 第 1 行）；
 * 3. 该语言不在这篇资料的 `runnable` 列表 → 渲染**禁用态** + Tooltip「该语言暂不支持站内运行」。
 *
 * 禁用按钮自己收不到 hover，所以套一层 `tabIndex={0}` 的 span 给 Tooltip 当触发器
 * （与组件库 gallery 的 Offline 示例同一做法）；键盘用户也能 Focus 到并看到原因。
 */
import { Play } from "lucide-react";

import { Button, Tooltip, TooltipProvider } from "@/components/ui";

import { isProgrammingLanguage, isRunnableLang, runnerLangOf, langLabel } from "../languages";
import { useRunnerEnabled } from "../settings";

/** §20.1 的固定 Tooltip 文案。 */
export const UNSUPPORTED_TOOLTIP = "该语言暂不支持站内运行";

export interface RunButtonProps {
  /** 代码围栏的语言（`python` / `js` / `cpp` …）。 */
  lang: string;
  /** 代码块原文（交给抽屉作为初值）。 */
  code: string;
  /** 资料的 front-matter `runnable`（`null` = 没标记，整页不出入口）。 */
  runnable: readonly string[] | null | undefined;
  /** 打开抽屉。 */
  onOpen: (payload: { lang: string; code: string }) => void;
}

export function RunButton({ lang, code, runnable, onOpen }: RunButtonProps) {
  const { enabled, supported } = useRunnerEnabled();

  if (!enabled || !supported) return null;
  if (!runnable || runnable.length === 0) return null;
  // `text` / `src` 这类排版块不是语言：不渲染任何按钮（§20.10 只针对"真语言"给禁用态）
  if (!isProgrammingLanguage(lang)) return null;

  const target = runnerLangOf(lang);
  const allowed = target !== null && isRunnableLang(lang, runnable);

  if (!allowed) {
    return (
      // 应用外壳没有全局 TooltipProvider（只有组件画廊里有），这里自带一层，
      // 免得为了一个禁用药水的 Tooltip 去改 M0 的 AppShell。
      <TooltipProvider>
        <Tooltip content={UNSUPPORTED_TOOLTIP}>
          <span className="runner-run__tip" tabIndex={0} role="button" aria-disabled="true">
            <Button
              size="sm"
              variant="ghost"
              iconLeft={<Play size={13} />}
              disabled
              aria-label={UNSUPPORTED_TOOLTIP}
            >
              运行
            </Button>
          </span>
        </Tooltip>
      </TooltipProvider>
    );
  }

  const label = target ? langLabel(target) : lang;

  return (
    <Button
      size="sm"
      variant="secondary"
      iconLeft={<Play size={13} />}
      className="runner-run__button"
      aria-label={`在浏览器内运行这段 ${label} 代码`}
      onClick={() => onOpen({ lang, code })}
    >
      运行
    </Button>
  );
}
