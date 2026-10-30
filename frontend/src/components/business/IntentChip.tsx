/**
 * IntentChip（业务组件，需求文档 §9.4「IntentChip：助手回复顶部显示识别到的意图
 * （"识别为：生成学习计划"），可点击纠正 → 提升可解释性」，Owner：助手模块 M4）。
 *
 * **为什么是只读的**（M4 决策，写进模块报告）：
 * `POST /api/agent/chat` 的请求体只有 `message` / `locale` / `page_context`（API.md §3.18），
 * 没有"指定意图"的字段；L1/L2 的判定结果由服务端决定。前端要"纠正"只能把用户的话重发一遍
 * 并祈祷命中规则，那不是纠正、是重试。契约里也没有纠错端点，所以按需求文档的退路
 * （"若无法纠正，显示为只读 chip 并注明识别为"）渲染成只读 chip：
 * 带 `识别为：` 前缀、置信度与来源（规则/模型），并靠 `title` 说明"意图由服务端判定"。
 * 要真正可纠正，需要契约层给 `AgentChatRequest` 加字段，属 CONTRACT_IMPACT。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——它不是控件，
 * 不接收指针事件、不可聚焦（读屏仍会念出 `aria-label`）。
 */
import { Sparkles } from "lucide-react";

import { cn } from "@/components/ui";

import "./chat.css";

export interface IntentChipProps {
  /** 已翻译的意图名，例如「生成学习计划」。 */
  label: string;
  /** 前缀，默认「识别为」。 */
  prefix?: string;
  /** 置信度 0–1；给了就显示百分比（规则命中恒 100%，由调用方决定要不要传）。 */
  confidence?: number | null;
  /** 已翻译的来源（规则 / 模型 / 兜底）。 */
  source?: string | null;
  /** 鼠标悬停的说明（例如"意图由服务端判定，暂不支持在页面上改"）。 */
  hint?: string;
  className?: string;
}

export function IntentChip({
  label,
  prefix = "识别为",
  confidence = null,
  source = null,
  hint,
  className,
}: IntentChipProps) {
  return (
    <span
      className={cn("intent-chip", className)}
      title={hint}
      aria-label={`${prefix}：${label}${source ? `（${source}）` : ""}`}
    >
      <Sparkles size={12} aria-hidden="true" />
      <span className="intent-chip__prefix" aria-hidden="true">
        {prefix}：
      </span>
      <span className="intent-chip__label">{label}</span>
      {confidence !== null ? (
        <span className="intent-chip__confidence tnum" aria-hidden="true">
          {Math.round(confidence * 100)}%
        </span>
      ) : null}
      {source ? (
        <>
          <span className="intent-chip__dot" aria-hidden="true">
            ·
          </span>
          <span className="intent-chip__source" aria-hidden="true">
            {source}
          </span>
        </>
      ) : null}
    </span>
  );
}
