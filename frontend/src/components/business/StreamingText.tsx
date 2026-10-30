/**
 * StreamingText（业务组件，需求文档 §9.4「StreamingText：逐字输出，结束后把光标换成
 * 「复制」按钮」，Owner：助手模块 M4）。
 *
 * 为什么复制按钮在**这里**而不是气泡里：它需要拿到"最终文本"，而文本就是本组件的 props；
 * 放进气泡会让气泡多一个只为复制而存在的 prop，也会让"结束后才有按钮"这条规则散落两处。
 *
 * 五态：
 * - hover：复制按钮自己的 hover（Button 提供）
 * - focus-visible：复制按钮的 2px 环
 * - active：复制按钮按下位移
 * - disabled：`streaming` 时不渲染复制按钮（等价于不可用），光标在代替它
 * - loading：`streaming` + 尚无文本 → 显示 `placeholder`（"正在思考…"）
 */
import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button, cn } from "@/components/ui";

import { copyToClipboard } from "./clipboard";
import "./chat.css";

export interface StreamingTextProps {
  /** 已收的全部增量文本。 */
  text: string;
  /** 还在流式（显示光标，隐藏复制按钮）。 */
  streaming: boolean;
  /** 流式中且一个字都还没到时的占位文案。 */
  placeholder?: string;
  copyLabel?: string;
  copiedLabel?: string;
  failedLabel?: string;
  className?: string;
}

export function StreamingText({
  text,
  streaming,
  placeholder,
  copyLabel = "复制",
  copiedLabel = "已复制",
  failedLabel = "复制失败",
  className,
}: StreamingTextProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const empty = text.length === 0;

  return (
    <div className={cn("stream-text", className)}>
      {streaming && empty ? (
        <p className="stream-text__placeholder" role="status">
          {placeholder}
        </p>
      ) : (
        <p className="stream-text__body">
          {text}
          {streaming ? <span className="stream-text__cursor" aria-hidden="true" /> : null}
        </p>
      )}

      {!streaming && !empty ? (
        <div className="stream-text__actions">
          <Button
            size="sm"
            variant="ghost"
            iconLeft={copyState === "copied" ? <Check size={13} /> : <Copy size={13} />}
            onClick={() => {
              void copyToClipboard(text).then((ok) => {
                setCopyState(ok ? "copied" : "failed");
                if (ok) window.setTimeout(() => setCopyState("idle"), 1600);
              });
            }}
          >
            {copyState === "copied"
              ? copiedLabel
              : copyState === "failed"
                ? failedLabel
                : copyLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
