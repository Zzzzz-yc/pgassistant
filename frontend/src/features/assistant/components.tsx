/**
 * 助手页的页面级块（Owner：助手模块 M4）。
 *
 * 这些不是"业务组件"（不被第二个模块复用），所以留在 `features/assistant/`；
 * 真正的业务组件（ChatBubble / IntentChip / StreamingText）在
 * `components/business/`，见 MODULES.md §1.2。
 *
 * 每个块都只吃 props：搜索结果的链接用 `resourceHref()` 拼、计划卡用 `useSaveDraft()`
 * 落库、错误卡只吃 `ApiError`。跳转用 `useNavigate()`（本页只有页面内部的两三个目的地）。
 */
import {
  ArrowRight,
  Ban,
  CalendarRange,
  Gauge,
  Info,
  Lightbulb,
  ListChecks,
  Search,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { ErrorState } from "@/components/business/ErrorState";
import { DurationLabel } from "@/components/business/DurationLabel";
import { TypeIcon } from "@/components/business/TypeIcon";
import { Badge, Button, IconButton, cn } from "@/components/ui";
import { useSaveDraft } from "@/features/assistant/hooks";
import type { PlanOutcome } from "@/features/assistant/types";
import { resourceHref } from "@/features/library/media";
import { ApiError } from "@/lib/http";
import type { Level, PlanDraft, SearchResponse } from "@/types/api";

/* ------------------------------------------------------------ 小工具 */

const NUMBER_FORMAT = new Intl.NumberFormat();

/** 12345 → `12,345`（token 累计用等宽数字，§9.3）。 */
export function formatCount(value: number): string {
  return NUMBER_FORMAT.format(Math.max(0, Math.round(value)));
}

/** `error.detail.retry_after`（秒）；没有就按错误码给默认值。 */
export function retryAfterSeconds(error: ApiError): number | null {
  const detail = error.detail;
  if (detail && typeof detail === "object" && "retry_after" in detail) {
    const raw = (detail as { retry_after?: unknown }).retry_after;
    if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) return Math.ceil(raw);
  }
  return error.code === "LLM_429" ? 30 : null;
}

/** 命中词在 `snippet` 里是 `[词]` 包裹（§13.2-2：**不是 HTML**），这里拆成 `<mark>`。 */
function highlightSnippet(snippet: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /\[([^\]]+)\]/g;
  let cursor = 0;
  let index = 0;
  let match = pattern.exec(snippet);
  while (match !== null) {
    if (match.index > cursor) parts.push(snippet.slice(cursor, match.index));
    parts.push(<mark key={`mark-${index}`}>{match[1]}</mark>);
    index += 1;
    cursor = match.index + match[0].length;
    match = pattern.exec(snippet);
  }
  if (cursor < snippet.length) parts.push(snippet.slice(cursor));
  return parts;
}

/* --------------------------------------------------------- 能力边界提示 */

export function BoundaryBar({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation("assistant");
  return (
    <div className="asst-boundary" role="note">
      <span className="asst-boundary__icon" aria-hidden="true">
        <Info size={15} />
      </span>
      <p className="asst-boundary__text">{t("boundary.text")}</p>
      <IconButton label={t("boundary.dismiss")} icon={<X size={14} />} size="sm" onClick={onClose} />
    </div>
  );
}

/* ------------------------------------------------------------ 模型未配置 */

export function ModelGuide({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { t } = useTranslation("assistant");
  return (
    <div className="asst-guide" role="note">
      <span className="asst-guide__icon" aria-hidden="true">
        <Lightbulb size={16} />
      </span>
      <div className="asst-guide__body">
        <p className="asst-guide__title">{t("guide.title")}</p>
        <p className="asst-guide__text">{t("guide.body")}</p>
      </div>
      <Button size="sm" variant="primary" onClick={onOpenSettings}>
        {t("guide.action")}
      </Button>
    </div>
  );
}

/* -------------------------------------------------------- 会话 token 累计 */

export function SessionTokens({ tokens }: { tokens: number }) {
  const { t } = useTranslation("assistant");
  if (tokens <= 0) return null;
  const heavy = tokens > 50_000;
  return (
    <div className={cn("asst-tokens", heavy && "asst-tokens--warn")} role="status">
      <Gauge size={13} aria-hidden="true" />
      <span className="asst-tokens__value tnum">{t("tokens.label", { tokens: formatCount(tokens) })}</span>
      {heavy ? <span className="asst-tokens__warn">{t("tokens.warn")}</span> : null}
    </div>
  );
}

/* -------------------------------------------------------------- 空状态卡 */

const EXAMPLE_KEYS = ["plan", "search", "advice", "refusal"] as const;
type ExampleKey = (typeof EXAMPLE_KEYS)[number];

const EXAMPLE_ICONS: Record<ExampleKey, typeof CalendarRange> = {
  plan: CalendarRange,
  search: Search,
  advice: Lightbulb,
  refusal: Ban,
};

export function ExampleCards({ onPick }: { onPick: (prompt: string) => void }) {
  const { t } = useTranslation("assistant");
  return (
    <div className="asst-empty">
      <div className="asst-empty__head">
        <h2 className="asst-empty__title">{t("empty.title")}</h2>
        <p className="asst-empty__note">{t("empty.note")}</p>
      </div>
      <ul className="asst-examples">
        {EXAMPLE_KEYS.map((key) => {
          const Icon = EXAMPLE_ICONS[key];
          const prompt = t(`empty.examples.${key}.prompt`);
          return (
            <li key={key}>
              <button
                type="button"
                className={cn("asst-example", `asst-example--${key}`)}
                onClick={() => onPick(prompt)}
              >
                <span className="asst-example__label">
                  <span className="asst-example__icon" aria-hidden="true">
                    <Icon size={15} />
                  </span>
                  {t(`empty.examples.${key}.label`)}
                </span>
                <span className="asst-example__prompt">{prompt}</span>
                <span className="asst-example__hint">{t(`empty.examples.${key}.hint`)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------ 检索结果卡 */

const LEVEL_LABEL_KEY: Record<Level, string> = {
  beginner: "level.beginner",
  intermediate: "level.intermediate",
  advanced: "level.advanced",
};

export function SearchResults({ data }: { data: SearchResponse }) {
  const { t } = useTranslation("assistant");
  const navigate = useNavigate();

  if (data.items.length === 0) {
    return (
      <div className="asst-search asst-search--empty">
        <p className="asst-search__head">{t("search.empty", { query: data.query })}</p>
        <p className="asst-search__note">{t("search.emptyNote")}</p>
      </div>
    );
  }

  return (
    <div className="asst-search">
      <p className="asst-search__head tnum">
        {t("search.head", { total: data.total, query: data.query })}
        {typeof data.took_ms === "number" ? (
          <span className="asst-search__took">{t("search.took", { ms: data.took_ms })}</span>
        ) : null}
      </p>

      <ul className="asst-search__list">
        {data.items.map((hit) => (
          <li key={hit.slug} className="asst-search__item">
            <a
              className="asst-search__link"
              href={resourceHref(hit.slug)}
              onClick={(event) => {
                event.preventDefault();
                navigate(resourceHref(hit.slug));
              }}
            >
              <span className="asst-search__title">{hit.title}</span>
              <span className="asst-search__snippet">{highlightSnippet(hit.snippet)}</span>
              <span className="asst-search__meta">
                <TypeIcon type={hit.type} showLabel />
                <DurationLabel minutes={hit.duration_min} />
                <Badge tone="accent">{t(LEVEL_LABEL_KEY[hit.level])}</Badge>
                <span className="asst-search__path">
                  {hit.stack} / {hit.category}
                </span>
              </span>
            </a>
          </li>
        ))}
      </ul>

      <p className="asst-search__note">{t("search.note")}</p>
    </div>
  );
}

/* ------------------------------------------------------------ 澄清选项卡 */

export function ClarifyOptions({
  options,
  onPick,
}: {
  options: readonly string[];
  onPick: (value: string) => void;
}) {
  const { t } = useTranslation("assistant");
  const visible = options.slice(0, 3);
  if (visible.length === 0) return null;
  return (
    <div className="asst-clarify">
      <p className="asst-clarify__hint">{t("clarify.hint")}</p>
      <ul className="asst-clarify__list">
        {visible.map((option) => (
          <li key={option}>
            <button
              type="button"
              className="asst-clarify__option"
              onClick={() => onPick(option)}
            >
              {option}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------ 计划草稿卡 */

/** 一个阶段最多列几条（草稿最多 40 条，全列会把气泡拉到几千像素）。 */
const ITEMS_PER_STAGE = 4;

export function PlanDraftCard({ draft }: { draft: PlanDraft }) {
  const { t } = useTranslation("assistant");
  const navigate = useNavigate();
  const save = useSaveDraft();
  const [savedId, setSavedId] = useState<number | null>(null);

  const totalItems = draft.stages.reduce((sum, stage) => sum + stage.items.length, 0);
  const minutes = draft.stages.reduce(
    (sum, stage) => sum + stage.items.reduce((inner, item) => inner + (item.est_minutes ?? 30), 0),
    0,
  );
  const saveError = save.error instanceof ApiError ? save.error : null;

  return (
    <div className="asst-plan">
      <p className="asst-plan__title">{draft.title}</p>
      <p className="asst-plan__goal">{draft.goal}</p>
      <p className="asst-plan__meta tnum">
        {t("plan.meta", {
          weeks: draft.weeks,
          hours: draft.weekly_hours,
          stages: draft.stages.length,
          items: totalItems,
          minutes: formatCount(minutes),
        })}
      </p>

      <ol className="asst-plan__stages">
        {draft.stages.map((stage, stageIndex) => {
          const hidden = stage.items.length - ITEMS_PER_STAGE;
          return (
            <li key={`${stage.title}-${stageIndex}`} className="asst-plan__stage">
              <p className="asst-plan__stage-head">
                <span className="asst-plan__index tnum">{stageIndex + 1}</span>
                <span className="asst-plan__stage-title">{stage.title}</span>
                <span className="asst-plan__weeks tnum">{t("plan.weeks", { weeks: stage.weeks })}</span>
              </p>
              <ul className="asst-plan__items">
                {stage.items.slice(0, ITEMS_PER_STAGE).map((item, itemIndex) => (
                  <li key={`${item.title}-${itemIndex}`} className="asst-plan__item">
                    <span className="asst-plan__item-title">{item.title}</span>
                    <span className="asst-plan__item-meta tnum">
                      {t("plan.minutes", { minutes: item.est_minutes ?? 30 })}
                    </span>
                  </li>
                ))}
              </ul>
              {hidden > 0 ? (
                <p className="asst-plan__more">{t("plan.more", { count: hidden })}</p>
              ) : null}
            </li>
          );
        })}
      </ol>

      <div className="asst-plan__actions">
        <Button
          variant="primary"
          loading={save.isPending}
          loadingLabel={t("plan.saving")}
          disabled={save.isPending || savedId !== null}
          iconLeft={<ListChecks size={14} aria-hidden="true" />}
          onClick={() => {
            save.mutate(draft, {
              onSuccess: (plan) => {
                setSavedId(plan.id);
                navigate(`/plans/${plan.id}`);
              },
            });
          }}
        >
          {save.isPending ? t("plan.saving") : savedId !== null ? t("plan.saved") : t("plan.save")}
        </Button>
        <Button
          variant="ghost"
          iconRight={<ArrowRight size={14} aria-hidden="true" />}
          onClick={() => navigate("/plans/new?from=assistant")}
        >
          {t("plan.preview")}
        </Button>
      </div>
      <p className="asst-plan__note">{t("plan.note")}</p>

      {saveError ? (
        <ErrorState
          code={saveError.code}
          message={saveError.message}
          hint={saveError.hint}
          detail={saveError.detail}
          requestId={saveError.requestId ?? null}
          retryLabel={t("plan.retry")}
          onRetry={() => save.mutate(draft)}
        />
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------ 计划摘要 */

/**
 * 计划自动落库后的**一句话结论 + 跳转**（C4）。
 *
 * 助手气泡只渲染这个摘要；完整草稿留在 `hooks.ts` 的模块级缓存里（保存失败时重试）。
 * 状态三选一：保存中 / 已保存（给 `/plans/{id}` 链接）/ 失败（给重试按钮）。
 */
export function PlanSummary({
  plan,
  onRetry,
}: {
  plan: PlanOutcome;
  onRetry: () => void;
}) {
  const { t } = useTranslation("assistant");
  const navigate = useNavigate();
  const hours = Math.max(1, Math.round(plan.minutes / 60));
  const saved = plan.saveState === "saved" && plan.planId !== null;
  const failed = plan.saveState === "failed";
  const params = { title: plan.title, stages: plan.stages, items: plan.items, hours };

  return (
    <div className="asst-plan asst-plan--summary">
      <p className="asst-plan__title">
        {saved
          ? t("plan.summarySaved", params)
          : failed
            ? t("plan.summaryFailed", params)
            : t("plan.summarySaving", params)}
      </p>
      <div className="asst-plan__actions">
        {saved ? (
          <Button
            variant="primary"
            iconRight={<ArrowRight size={14} aria-hidden="true" />}
            onClick={() => navigate(`/plans/${plan.planId}`)}
          >
            {t("plan.open")}
          </Button>
        ) : failed ? (
          <Button
            variant="primary"
            iconLeft={<ListChecks size={14} aria-hidden="true" />}
            onClick={onRetry}
          >
            {t("plan.retrySave")}
          </Button>
        ) : (
          <span className="asst-plan__note" role="status">
            {t("plan.summaryWaiting")}
          </span>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- 错误卡片 */

/** 这些码的动作是"去设置页"，其余码按 `errors.json` 的 action 走。 */
const SETTINGS_CODES = new Set(["LLM_401", "LLM_NOT_CONFIGURED", "NO_EMBEDDING"]);

export interface AssistantErrorProps {
  error: ApiError;
  /** 冷却剩余秒数（>0 时不给重试按钮：点一下就是又一次 429）。 */
  cooldownLeft: number;
  onRetry: () => void;
  onOpenSettings: () => void;
}

export function AssistantError({
  error,
  cooldownLeft,
  onRetry,
  onOpenSettings,
}: AssistantErrorProps) {
  const { t } = useTranslation("assistant");
  const needsSettings = SETTINGS_CODES.has(error.code);
  const wait = retryAfterSeconds(error);

  let hint: string | undefined = error.hint ?? undefined;
  if (error.code === "RATE_LIMITED" || error.code === "LLM_429") {
    const seconds = wait ?? 30;
    hint = t("error.wait", { seconds });
  }
  if (error.code === "NETWORK_ERROR") {
    hint = t("error.networkHint");
  }

  return (
    <div className="asst-error">
      <ErrorState
        title={t("error.title")}
        code={error.code}
        hint={hint}
        detail={error.detail}
        requestId={error.requestId ?? null}
        retryLabel={t("error.retry")}
        copyLabel={t("error.copy")}
        copiedLabel={t("error.copied")}
        onRetry={cooldownLeft > 0 ? undefined : onRetry}
      />
      {needsSettings ? (
        <div className="asst-error__actions">
          <Button size="sm" variant="secondary" onClick={onOpenSettings}>
            {t("error.settings")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
