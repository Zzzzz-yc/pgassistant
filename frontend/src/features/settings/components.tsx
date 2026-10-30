/**
 * 设置页内部组件（Owner：打磨模块 M5）。
 *
 * `components/business/SettingsSection` 在本模块开工时**不存在**（契约 `MODULES.md`
 * §1.2 把它列在 `components/business` 下，但那属于组件库的文件，M5 不改别人的目录），
 * 所以按调度要求在这里自建同职责的组件，对外仍是"标题 + 说明 + 行"的分组容器。
 *
 * 五态落点（§9.4：禁止原生控件裸奔）：
 * - hover / focus-visible / active / disabled / loading 全部由 `components/ui` 的
 *   Button / Input / Select / Switch / Slider 承担，本文件只做布局与状态展示；
 * - `TestResultLine` 的 loading 是 Spinner + `aria-live="polite"`，不是把按钮转圈；
 * - `IndexProgress` 的进度条支持不确定态（`ProgressBar` 的 `value=null`）。
 */
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Badge, ProgressBar, Spinner } from "@/components/ui";
import { ApiError } from "@/lib/http";
import type { IndexStatus, PrivateRescanResponse, SettingsTestResponse } from "@/types/api";

/* --------------------------------------------------------------- 分组容器 */

export interface SettingsSectionProps {
  /** `h2` 的 id，供 `aria-labelledby` 用。 */
  id: string;
  title: ReactNode;
  description?: ReactNode;
  /** 标题右侧的状态徽标 / 操作。 */
  status?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}

export function SettingsSection({
  id,
  title,
  description,
  status,
  children,
  footer,
}: SettingsSectionProps) {
  const headingId = `${id}-title`;
  return (
    <section className="settings-section" aria-labelledby={headingId}>
      <header className="settings-section__head">
        <div className="settings-section__heading">
          <h2 className="settings-section__title" id={headingId}>
            {title}
          </h2>
          {description ? <p className="settings-section__desc">{description}</p> : null}
        </div>
        {status ? <div className="settings-section__status">{status}</div> : null}
      </header>

      <div className="settings-section__body">{children}</div>
      {footer ? <footer className="settings-section__foot">{footer}</footer> : null}
    </section>
  );
}

/* ------------------------------------------------------------------ 行 */

export interface SettingRowProps {
  /** 行左侧的 label 文本（不可点击时也能当视觉标签）。 */
  label: ReactNode;
  /** 关联的控件 id：给了就渲染 `<label for>`（§9.7 label 与 input 关联）。 */
  htmlFor?: string;
  hint?: ReactNode;
  /** 行内错误（红色 + `role="alert"`）。 */
  error?: ReactNode;
  /** 备注：只在某些状态出现的一行说明。 */
  note?: ReactNode;
  children: ReactNode;
}

export function SettingRow({ label, htmlFor, hint, error, note, children }: SettingRowProps) {
  const labelNode =
    htmlFor === undefined ? (
      <span className="settings-row__label">{label}</span>
    ) : (
      <label className="settings-row__label" htmlFor={htmlFor}>
        {label}
      </label>
    );

  return (
    <div className="settings-row">
      <div className="settings-row__meta">{labelNode}</div>
      <div className="settings-row__control">
        {children}
        {note ? <p className="settings-row__note">{note}</p> : null}
        {error ? (
          <p className="settings-row__error" role="alert">
            {error}
          </p>
        ) : null}
        {hint ? <p className="settings-row__hint">{hint}</p> : null}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- 提示条 */

export type NoticeTone = "info" | "success" | "warn" | "danger";

export interface InlineNoticeProps {
  tone?: NoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  /** 右侧动作（例如"重试"）。 */
  action?: ReactNode;
}

export function InlineNotice({ tone = "info", title, children, action }: InlineNoticeProps) {
  return (
    <div className={`settings-notice settings-notice--${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <div className="settings-notice__body">
        {title ? <p className="settings-notice__title">{title}</p> : null}
        {children ? <div className="settings-notice__text">{children}</div> : null}
      </div>
      {action ? <div className="settings-notice__action">{action}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------------ 徽标小件 */

export function StateBadge({ on, onLabel, offLabel }: { on: boolean; onLabel: string; offLabel: string }) {
  return (
    <Badge tone={on ? "primary" : "neutral"} size="sm">
      {on ? onLabel : offLabel}
    </Badge>
  );
}

/* --------------------------------------------------------- 探针结果行 */

export interface TestResultLineProps {
  /** 探针结果（`ok` 可能为 false——探针失败也是 200）。 */
  result: SettingsTestResponse | null;
  /** 请求本身失败（网络 / 4xx / 5xx）。 */
  error: unknown;
  pending: boolean;
}

/**
 * 探针结果：`ok=true` 显示"正常 + 耗时 + 模型"，`ok=false` 显示后端的失败原因与错误码。
 *
 * 用 `role="status"` + `aria-live="polite"`：读屏在结果到达时读一句，不打断输入。
 */
export function TestResultLine({ result, error, pending }: TestResultLineProps) {
  const { t } = useTranslation("settings");

  if (pending) {
    return (
      <p className="settings-test settings-test--pending" role="status">
        <Spinner size="sm" />
        <span>{t("test.pending")}</span>
      </p>
    );
  }

  if (error) {
    const code = error instanceof ApiError ? error.code : "UNKNOWN";
    const message = error instanceof ApiError ? error.message : String(error);
    return (
      <p className="settings-test settings-test--fail" role="status">
        <span className="settings-test__label">{t("test.requestFailed")}</span>
        <span>{message}</span>
        <code className="settings-test__code">{code}</code>
      </p>
    );
  }

  if (!result) return null;

  if (!result.ok) {
    return (
      <p className="settings-test settings-test--fail" role="status">
        <span className="settings-test__label">{t("test.failed")}</span>
        <span>{result.message || t("test.failedFallback")}</span>
        {result.error_code ? <code className="settings-test__code">{result.error_code}</code> : null}
        <span className="settings-test__meta tnum">{t("test.latency", { ms: result.latency_ms })}</span>
      </p>
    );
  }

  return (
    <p className="settings-test settings-test--ok" role="status">
      <span className="settings-test__label">{t("test.ok")}</span>
      <span className="settings-test__meta tnum">{t("test.latency", { ms: result.latency_ms })}</span>
      {result.model ? (
        <span className="settings-test__meta">{t("test.model", { model: result.model })}</span>
      ) : null}
    </p>
  );
}

/* ------------------------------------------------------------- 索引进度 */

export interface IndexProgressProps {
  status: IndexStatus | null;
  /** 本轮是否刚发起（决定空状态要不要显示提示）。 */
  started: boolean;
}

export function IndexProgress({ status, started }: IndexProgressProps) {
  const { t } = useTranslation("settings");

  if (!status) {
    return started ? (
      <p className="settings-index__line" role="status">
        <Spinner size="sm" />
        <span>{t("index.waiting")}</span>
      </p>
    ) : null;
  }

  const files = t("index.files", { done: status.files_done, total: status.files_total });

  if (status.state === "running") {
    return (
      <div className="settings-index" role="status" aria-live="polite">
        <ProgressBar
          value={status.percent}
          label={t("index.running")}
          valueLabel={`${Math.round(status.percent)}%`}
        />
        <p className="settings-index__line tnum">{files}</p>
      </div>
    );
  }

  if (status.state === "failed") {
    return (
      <InlineNotice tone="danger" title={t("index.failed")}>
        <p>{status.error || t("index.failedFallback")}</p>
        {status.files_total > 0 ? <p className="tnum">{files}</p> : null}
      </InlineNotice>
    );
  }

  // idle：成功终态是 idle + percent===100（IndexRunState 没有 done）
  if (status.percent >= 100) {
    return (
      <div className="settings-index" role="status">
        <ProgressBar value={100} label={t("index.done")} valueLabel="100%" />
        <p className="settings-index__line tnum">{files}</p>
      </div>
    );
  }

  // idle 且没跑过：只说明上次索引时间
  return status.last_indexed_at ? (
    <p className="settings-index__line" role="status">
      {t("index.lastIndexed", { time: formatTime(status.last_indexed_at) })}
    </p>
  ) : (
    <p className="settings-index__line" role="status">
      {t("index.never")}
    </p>
  );
}

/* ---------------------------------------------------------- 扫描结果 */

export interface ScanResultProps {
  report: PrivateRescanResponse;
}

/** 「最近一次扫描结果」：新增 / 更新 / 删除 / 跳过四个数 + 根目录 + 耗时。 */
export function ScanResult({ report }: ScanResultProps) {
  const { t } = useTranslation("settings");
  const cells: Array<{ key: string; label: string; value: number; tone?: "add" | "warn" }> = [
    { key: "added", label: t("private.result.added"), value: report.added, tone: "add" },
    { key: "updated", label: t("private.result.updated"), value: report.updated },
    { key: "removed", label: t("private.result.removed") , value: report.removed, tone: "warn" },
    { key: "skipped", label: t("private.result.skipped"), value: report.skipped },
  ];

  return (
    <div className="settings-scan" role="status">
      <p className="settings-scan__head">
        {t("private.result.title")}
        <span className="settings-scan__meta tnum">
          {t("private.result.scanned", { count: report.scanned })}
          {" · "}
          {t("private.result.took", { ms: report.took_ms })}
        </span>
      </p>

      <dl className="settings-scan__grid">
        {cells.map((cell) => (
          <div
            className={
              cell.value > 0 && cell.tone ? `settings-scan__cell is-${cell.tone}` : "settings-scan__cell"
            }
            key={cell.key}
          >
            <dt>{cell.label}</dt>
            <dd className="tnum">{cell.value}</dd>
          </div>
        ))}
      </dl>

      <p className="settings-scan__root">
        {t("private.result.root")}
        <code>{report.root}</code>
      </p>

      {report.errors.length > 0 ? (
        <details className="settings-scan__errors">
          <summary>{t("private.result.errors", { count: report.errors.length })}</summary>
          <ul>
            {report.errors.slice(0, 10).map((line) => (
              <li key={line}>
                <code>{line}</code>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- 工具 */

/** `2026-10-30T12:05:00+00:00` → 本地 `2026-10-30 20:05`；解析失败原文返回。 */
export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}
