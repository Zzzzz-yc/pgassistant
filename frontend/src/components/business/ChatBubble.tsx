/**
 * ChatBubble（业务组件，需求文档 §9.4「ChatBubble：用户右对齐、助手左对齐带头像；流式时
 * 显示光标」，Owner：助手模块 M4，落点见 MODULES.md §1.2）。
 *
 * 边界：**纯展示**。内容全部由 children / header / footer 传入；不发请求、不读路由、
 * 不读写 localStorage。光标在 `StreamingText` 里，这里只负责"谁发的、往哪边靠"。
 *
 * 五态：
 * - hover：气泡边框向 primary（仅助手卡通；用户卡是实色底，不再变）
 * - focus-visible：容器无动作，焦点环落在内部的链接/按钮上（§9.7）
 * - active：**不适用**（气泡不可点）
 * - disabled：**不适用**（不可交互）
 * - loading：`pending` → `aria-busy="true"` + 左侧一道呼吸竖条，提示"正在说"
 */
import type { ReactNode } from "react";

import { Avatar, cn } from "@/components/ui";

import "./chat.css";

export type ChatRole = "user" | "assistant";

export interface ChatBubbleProps {
  role: ChatRole;
  children: ReactNode;
  /** 助手头像的名字（首字块来源，也是读屏名）；用户侧不渲染头像。 */
  name?: string;
  /** 气泡上方（助手的意图 chip）。 */
  header?: ReactNode;
  /** 气泡下方（复制按钮、已中止说明、错误动作）。 */
  footer?: ReactNode;
  /** 流式中。 */
  pending?: boolean;
  /** 出错：整体换成危险色边框（错误卡片自带 ErrorState 的结构）。 */
  tone?: "default" | "error";
  className?: string;
}

export function ChatBubble({
  role,
  children,
  name = "助手",
  header,
  footer,
  pending = false,
  tone = "default",
  className,
}: ChatBubbleProps) {
  const isUser = role === "user";
  return (
    <div
      className={cn(
        "chat-bubble",
        `chat-bubble--${role}`,
        tone === "error" && "chat-bubble--error",
        className,
      )}
      data-role={role}
      aria-busy={pending || undefined}
    >
      {isUser ? null : <Avatar className="chat-bubble__avatar" name={name} size="sm" />}

      <div className="chat-bubble__main">
        {header ? <div className="chat-bubble__header">{header}</div> : null}
        <div className="chat-bubble__content">{children}</div>
        {footer ? <div className="chat-bubble__footer">{footer}</div> : null}
      </div>
    </div>
  );
}
