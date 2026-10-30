/**
 * 助手对话 —— `/assistant`（Owner：助手模块 M4）。
 *
 * 需求落点：
 * - §9.5「助手对话」：空状态 4 张示例卡（对应 4 个意图）、有消息后顶部固定「能力边界」提示条
 *   （可关闭，localStorage 记住）；
 * - §7.10：页面上显示本次会话累计 token（估算即可），超过 50k 提示「本次会话消耗较大」；
 * - §9.6：Loading / Empty / Error / Partial / Offline 五态都有落点——未配模型是 **Offline**
 *   的助手版（引导卡片 + 去设置，不报错，浏览与检索照常）；
 * - §9.7：Enter 发送 / Shift+Enter 换行 / 发送后焦点留在输入框 / 所有控件可 Tab。
 *
 * 数据流：输入 → `useAgentStream().send` → `chatStream`（`lib/http.ts::readSSE`）→
 * `intent` 帧进 IntentChip、`delta` 帧逐字追加、`done` 帧按 `kind` 分派
 * （`text` / `search` / `plan_draft` / `clarify`）、`error` 帧换成错误卡片。
 *
 * 一条硬边界：**本页不做意图纠正**。`AgentChatRequest` 没有"指定意图"的字段（API.md §3.18），
 * 所以 IntentChip 是只读的（组件头注释里写了理由），要纠正得先改契约。
 */
import { SendHorizonal, Square } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { ChatBubble } from "@/components/business/ChatBubble";
import { IntentChip } from "@/components/business/IntentChip";
import { StreamingText } from "@/components/business/StreamingText";
import { Button, Textarea } from "@/components/ui";
import {
  AssistantError,
  BoundaryBar,
  ClarifyOptions,
  ExampleCards,
  ModelGuide,
  PlanDraftCard,
  SearchResults,
  SessionTokens,
  retryAfterSeconds,
} from "@/features/assistant/components";
import { isPlanDraftResult, isSearchResult, useAgentStream } from "@/features/assistant/hooks";
import { sessionTokens, type AssistantMessage } from "@/features/assistant/types";
import { useHealth } from "@/lib/query";

import "@/features/assistant/assistant.css";

/** 提示条"已关闭"记在这里（只有这一个键；令牌绝不进 localStorage，§13.7）。 */
const BOUNDARY_KEY = "assistant_boundary_v1";

function boundaryDismissed(): boolean {
  try {
    return window.localStorage.getItem(BOUNDARY_KEY) === "1";
  } catch {
    // 隐私模式下读不到：当作已关闭，别每次渲染都弹一条关不掉的提示
    return true;
  }
}

function rememberBoundaryDismissed(): void {
  try {
    window.localStorage.setItem(BOUNDARY_KEY, "1");
  } catch {
    // 存不下就只关本次会话
  }
}

export default function AssistantPage() {
  const { t } = useTranslation("assistant");
  const navigate = useNavigate();
  const health = useHealth();
  const stream = useAgentStream();

  const [input, setInput] = useState("");
  const [boundaryOpen, setBoundaryOpen] = useState(() => !boundaryDismissed());
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const handledError = useRef("");

  const messages = stream.messages;
  const tokens = sessionTokens(messages);
  const cooldownLeft = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));
  /** 未配模型（`/api/health.model_configured=false`）：只提示，不禁用输入（检索/问候照常）。 */
  const modelMissing = health.data?.model_configured === false;
  const canSend = input.trim().length > 0 && !stream.streaming && cooldownLeft === 0;
  /** 超出 L0 上限的字数（>0 时提示会被截断，但不拦发送，D26）。 */
  const overLimit = Math.max(0, input.trim().length - 500);

  /* 冷却倒计时：只在需要时挂一个 1s 的 interval */
  useEffect(() => {
    if (cooldownUntil <= Date.now()) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [cooldownUntil]);

  /**
   * 429 的冷却：`RATE_LIMITED`（本地令牌桶）与 `LLM_429`（上游限流）都带 `retry_after`。
   * 用 ref 去重，避免流式文本不断触发 messages 变化时反复重置倒计时。
   */
  useEffect(() => {
    const last = messages[messages.length - 1];
    if (!last || last.role !== "assistant" || !last.error) return;
    const key = `${last.id}:${last.error.code}`;
    if (handledError.current === key) return;
    handledError.current = key;
    if (last.error.code === "RATE_LIMITED" || last.error.code === "LLM_429") {
      const seconds = retryAfterSeconds(last.error) ?? 30;
      setCooldownUntil(Date.now() + seconds * 1000);
      setNow(Date.now());
    }
  }, [messages]);

  /* 贴底滚动：用户主动往上翻时不抢滚动条 */
  useEffect(() => {
    const element = listRef.current;
    if (!element || !stickToBottom.current) return;
    element.scrollTop = element.scrollHeight;
  }, [messages]);

  const focusInput = () => {
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const fillInput = (value: string) => {
    setInput(value);
    focusInput();
  };

  const submit = () => {
    if (!canSend) return;
    stream.send(input);
    setInput("");
    stickToBottom.current = true;
    focusInput();
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return; // Shift+Enter 换行
    // 输入法候选阶段按 Enter 是"上屏"，不是"发送"
    if ((event.nativeEvent as unknown as { isComposing?: boolean }).isComposing) return;
    event.preventDefault();
    submit();
  };

  const assistantName = t("bubble.assistantName");

  const renderAssistant = (message: AssistantMessage) => {
    const intent = message.intent;
    const header = intent ? (
      <IntentChip
        label={t(`intent.labels.${intent.intent}`)}
        prefix={t("intent.prefix")}
        // 规则命中恒 100%，显示反而像噪音；只有模型判定时标置信度
        confidence={intent.source === "llm" ? intent.confidence : null}
        source={t(`intent.source.${intent.source}`)}
        hint={t("intent.hint")}
      />
    ) : null;

    if (message.error) {
      // 出错时**不显示意图 chip**：流前失败（如 429/401）时后端只会补一个兜底的
      // `intent=CLARIFY`，"识别为：需要澄清"会误导用户以为是自己问得不清楚。
      return (
        <ChatBubble role="assistant" tone="error" name={assistantName}>
          <AssistantError
            error={message.error}
            cooldownLeft={cooldownLeft}
            onRetry={() => stream.retry(message.id)}
            onOpenSettings={() => navigate("/settings")}
          />
        </ChatBubble>
      );
    }

    const showText = message.streaming || message.text.length > 0;
    const footer = message.aborted ? (
      <span className="asst-meta" role="status">
        {t("bubble.aborted")}
      </span>
    ) : message.finishReason === "length" ? (
      <span className="asst-meta">{t("bubble.truncated")}</span>
    ) : null;

    return (
      <ChatBubble
        role="assistant"
        name={assistantName}
        header={header}
        footer={footer}
        pending={message.streaming}
      >
        {showText ? (
          <StreamingText
            text={message.text}
            streaming={message.streaming}
            placeholder={t("bubble.thinking")}
            copyLabel={t("bubble.copy")}
            copiedLabel={t("bubble.copied")}
            failedLabel={t("bubble.copyFailed")}
          />
        ) : null}

        {message.kind === "search" && isSearchResult(message.result) ? (
          <SearchResults data={message.result} />
        ) : null}

        {message.kind === "plan_draft" && isPlanDraftResult(message.result) ? (
          <PlanDraftCard draft={message.result} />
        ) : null}

        {message.kind === "clarify" ? (
          <ClarifyOptions
            options={intent?.clarify_options ?? []}
            onPick={(value) => fillInput(value)}
          />
        ) : null}
      </ChatBubble>
    );
  };

  return (
    <section className="page page-enter asst">
      <header className="asst__head">
        <div className="asst__intro">
          <h1 className="page__title asst__title">{t("title")}</h1>
          <p className="asst__subtitle">{t("subtitle")}</p>
        </div>
        <SessionTokens tokens={tokens} />
      </header>

      {modelMissing ? <ModelGuide onOpenSettings={() => navigate("/settings")} /> : null}

      {boundaryOpen ? (
        <BoundaryBar
          onClose={() => {
            setBoundaryOpen(false);
            rememberBoundaryDismissed();
          }}
        />
      ) : null}

      <div
        className="asst__scroll"
        ref={listRef}
        onScroll={() => {
          const element = listRef.current;
          if (!element) return;
          stickToBottom.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 96;
        }}
      >
        {messages.length === 0 ? (
          <ExampleCards onPick={(prompt) => fillInput(prompt)} />
        ) : (
          <ol className="asst__list" role="log" aria-label={t("thread.label")}>
            {messages.map((message) => (
              <li key={message.id} className="asst__list-item">
                {message.role === "user" ? (
                  <ChatBubble role="user">
                    <p className="asst-user-text">{message.text}</p>
                  </ChatBubble>
                ) : (
                  renderAssistant(message)
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      <form
        className="asst__composer"
        aria-label={t("composer.label")}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Textarea
          ref={textareaRef}
          rows={2}
          value={input}
          placeholder={t("composer.placeholder")}
          aria-label={t("composer.label")}
          fieldClassName="asst__field"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="asst__composer-row">
          <p className="asst__hint">{t("composer.hint")}</p>

          {/* D26：>500 字不是错误，后端在 L0 截断后照常 200；这里只提前告知，不拦输入 */}
          {overLimit > 0 ? (
            <span className="asst__overlimit tnum" role="status">
              {t("composer.truncate", { count: overLimit })}
            </span>
          ) : null}

          {cooldownLeft > 0 ? (
            <span className="asst__cooldown tnum" role="status">
              {t("composer.cooldown", { seconds: cooldownLeft })}
            </span>
          ) : null}

          {stream.streaming ? (
            <Button
              variant="secondary"
              onClick={stream.abort}
              iconLeft={<Square size={13} aria-hidden="true" />}
            >
              {t("composer.abort")}
            </Button>
          ) : (
            <Button
              type="submit"
              variant="primary"
              disabled={!canSend}
              iconRight={<SendHorizonal size={14} aria-hidden="true" />}
            >
              {cooldownLeft > 0
                ? t("composer.waitSend", { seconds: cooldownLeft })
                : t("composer.send")}
            </Button>
          )}
        </div>
      </form>
    </section>
  );
}
