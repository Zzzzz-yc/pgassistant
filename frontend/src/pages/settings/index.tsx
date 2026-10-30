/**
 * 设置 —— `/settings`（Owner：打磨模块 M5，需求文档 §9.5「设置」/ §10 / §13.8）。
 *
 * 四个 Section：模型 / 语义检索 / 私有资料 / 通用。所有字段都来自
 * `GET /api/settings`（`api_key` 只有掩码），写入一律走 `PUT` 的**局部更新**，
 * 只发用户真正改过的组——这样「保存模型」不会把 embedding 的配置一起覆盖掉。
 *
 * 六条硬约束（改之前先读）：
 * 1. **密钥只写不读**：`api_key` 输入框 `value` 恒为空串，掩码只出现在 placeholder
 *    和「当前密钥」一行；保存成功立刻清空输入框（§13.1）。
 * 2. **探针失败是 200**：`POST /api/settings/test` 的 `ok=false` 走正常返回，
 *    不能当异常处理，否则用户看不到「密钥无效」这类原因（API.md §3.21）。
 * 3. **索引没有 done 态**：`IndexRunState = idle|running|failed`，成功终态是
 *    `idle && percent===100`（`schemas/models.py`），轮询在非 running 时停。
 * 4. **409 不是失败**：`INDEX_BUILDING` 的 `detail.task_id` 是正在跑的任务，
 *    接住它继续轮询；`RATE_LIMITED` 按 `detail.retry_after` 倒计时禁用按钮。
 * 5. **代码运行开关要双写**：`PUT code_runner_enabled` + `localStorage.runner_enabled`
 *    + `dispatch("pgassistant:runner-settings")`，三件事必须一起做，否则算法页不刷新
 *    （键名与事件名由 `features/runner/settings.ts` 定义，本页只读不改）。
 * 6. **数据重置不伪造**：后端没有清空数据的端点，入口保持禁用并写明原因。
 */
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Database,
  FolderOpen,
  Gauge,
  KeyRound,
  Languages,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Terminal,
} from "lucide-react";

import { ErrorState } from "@/components/business/ErrorState";
import {
  Badge,
  Button,
  Input,
  Select,
  Slider,
  Skeleton,
  Switch,
  Tooltip,
  TooltipProvider,
} from "@/components/ui";
import {
  IndexProgress,
  InlineNotice,
  ScanResult,
  SettingRow,
  SettingsSection,
  StateBadge,
  TestResultLine,
  formatTime,
} from "@/features/settings/components";
import {
  useIndexStatus,
  usePrivateScan,
  useRebuildIndex,
  useSaveSettings,
  useSettings,
  useTestConnection,
} from "@/features/settings/hooks";
import { RUNNER_ENABLED_KEY, RUNNER_SETTINGS_EVENT } from "@/features/runner/settings";
import { errorAction, errorMessage, setLocale } from "@/i18n";
import { ApiError } from "@/lib/http";
import { useHealth, useToast } from "@/lib/query";
import type {
  EmbeddingConfigWrite,
  IndexMode,
  LLMConfigWrite,
  Locale,
  PrivateRescanResponse,
  SettingsRead,
} from "@/types/api";

import "@/features/settings/settings.css";

/** `http://` / `https://` 开头（与 `schemas/models.py` 的 `HTTP_URL_PATTERN` 同口径）。 */
const URL_PATTERN = /^https?:\/\/\S+$/i;
/** 绝对路径：`C:\…`、`\\server\…`、`/…`。 */
const ABS_PATH_PATTERN = /^([A-Za-z]:[\\/]|\\\\|\/)/;
const MAX_TOKENS_MIN = 256;
const MAX_TOKENS_MAX = 8192;

interface LLMForm {
  base_url: string;
  model: string;
  temperature: number;
  max_tokens: string;
  api_key: string;
}

interface EmbeddingForm {
  base_url: string;
  model: string;
  dim: string;
  api_key: string;
}

type FieldErrors = Record<string, string>;

function llmFormOf(settings: SettingsRead | undefined): LLMForm {
  return {
    base_url: settings?.llm.base_url ?? "",
    model: settings?.llm.model ?? "",
    temperature: settings?.llm.temperature ?? 0.3,
    max_tokens: String(settings?.llm.max_tokens ?? 1024),
    api_key: "",
  };
}

function embeddingFormOf(settings: SettingsRead | undefined): EmbeddingForm {
  return {
    base_url: settings?.embedding.base_url ?? "",
    model: settings?.embedding.model ?? "",
    dim: settings?.embedding.dim === null || settings?.embedding.dim === undefined
      ? ""
      : String(settings.embedding.dim),
    api_key: "",
  };
}

/** 3s 没加载完就补一句「仍在加载…」（§9.6 Loading）。 */
function useSlowHint(active: boolean, delayMs = 3_000): boolean {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!active) {
      setSlow(false);
      return;
    }
    const timer = window.setTimeout(() => setSlow(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [active, delayMs]);
  return slow;
}

/** `detail.retry_after`（秒）→ 至少 1 秒；读不到用 60s 兜底（限流是 1 次 / 5 分钟）。 */
function readRetryAfter(error: unknown): number {
  if (error instanceof ApiError) {
    const detail = error.detail;
    if (detail && typeof detail === "object" && "retry_after" in detail) {
      const value = Number((detail as { retry_after?: unknown }).retry_after);
      if (Number.isFinite(value) && value > 0) return Math.ceil(value);
    }
  }
  return 60;
}

function readTaskId(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = error.detail;
  if (detail && typeof detail === "object" && "task_id" in detail) {
    const value = (detail as { task_id?: unknown }).task_id;
    return typeof value === "string" && value ? value : null;
  }
  return null;
}

/** 写 `localStorage.runner_enabled` 并通知同标签页的算法页（`features/runner/settings.ts`）。 */
function syncRunnerFlag(enabled: boolean): void {
  try {
    window.localStorage.setItem(RUNNER_ENABLED_KEY, enabled ? "true" : "false");
  } catch {
    // 隐私模式写不进去：后端已经落了库，刷新后以设置页为准
  }
  window.dispatchEvent(new Event(RUNNER_SETTINGS_EVENT));
}

export default function SettingsPage() {
  const { t, i18n } = useTranslation(["settings", "common"]);
  const toastApi = useToast();
  const formId = useId();

  const settings = useSettings();
  const health = useHealth();
  const saveSettings = useSaveSettings();
  const testLlm = useTestConnection();
  const testEmbedding = useTestConnection();
  const rebuildMutation = useRebuildIndex();
  const privateScan = usePrivateScan();

  const [llmForm, setLlmForm] = useState<LLMForm>(() => llmFormOf(undefined));
  const [llmErrors, setLlmErrors] = useState<FieldErrors>({});
  const [embeddingForm, setEmbeddingForm] = useState<EmbeddingForm>(() => embeddingFormOf(undefined));
  const [embeddingErrors, setEmbeddingErrors] = useState<FieldErrors>({});
  const [privateDir, setPrivateDir] = useState("");
  const [privateError, setPrivateError] = useState<string | null>(null);
  const [scanReport, setScanReport] = useState<PrivateRescanResponse | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [indexStarted, setIndexStarted] = useState(false);
  const [rebuildMode, setRebuildMode] = useState<IndexMode | null>(null);
  const [retryUntil, setRetryUntil] = useState(0);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const [runnerBusy, setRunnerBusy] = useState(false);
  const [languageBusy, setLanguageBusy] = useState(false);

  const data = settings.data;
  const hydrated = useRef(false);
  const completedTask = useRef<string | null>(null);

  /* ------------------------------------------------------- 首次数据回填 */
  useEffect(() => {
    if (!data || hydrated.current) return;
    hydrated.current = true;
    setLlmForm(llmFormOf(data));
    setEmbeddingForm(embeddingFormOf(data));
    setPrivateDir(data.private_dir ?? "");
  }, [data]);

  /* ------------------------------------------------------------ 轮询 */
  const indexStatus = useIndexStatus(taskId);
  const status = indexStatus.data ?? null;

  useEffect(() => {
    if (!status || !taskId) return;
    const finished = status.state === "idle" && status.percent >= 100;
    if (!finished || completedTask.current === taskId) return;
    completedTask.current = taskId;
    toastApi.push({
      kind: "success",
      message: t("index.doneToast", { total: status.files_total }),
    });
  }, [status, taskId, toastApi, t]);

  /* --------------------------------------------------- 限流倒计时 tick */
  useEffect(() => {
    if (!retryUntil) return;
    if (Date.now() >= retryUntil) {
      setRetryUntil(0);
      return;
    }
    const timer = window.setInterval(() => setNowTick(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [retryUntil]);

  const retryLeft = retryUntil ? Math.max(0, Math.ceil((retryUntil - nowTick) / 1_000)) : 0;

  /* -------------------------------------------------------- 脏标记 */
  const llmDirty =
    !!data &&
    (llmForm.base_url !== (data.llm.base_url ?? "") ||
      llmForm.model !== (data.llm.model ?? "") ||
      llmForm.temperature !== data.llm.temperature ||
      llmForm.max_tokens !== String(data.llm.max_tokens) ||
      llmForm.api_key !== "");
  const embeddingDirty =
    !!data &&
    (embeddingForm.base_url !== (data.embedding.base_url ?? "") ||
      embeddingForm.model !== (data.embedding.model ?? "") ||
      embeddingForm.dim !==
        (data.embedding.dim === null || data.embedding.dim === undefined
          ? ""
          : String(data.embedding.dim)) ||
      embeddingForm.api_key !== "");
  const privateDirty = !!data && privateDir !== (data.private_dir ?? "");
  const anyDirty = llmDirty || embeddingDirty || privateDirty;

  /* --------------------------------------------- 离开页面前提醒（可选） */
  useEffect(() => {
    if (!anyDirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [anyDirty]);

  const slow = useSlowHint(settings.isLoading);

  /* ------------------------------------------------------------- 动作 */

  const validateLlm = (): FieldErrors => {
    const errors: FieldErrors = {};
    if (!llmForm.base_url.trim()) errors.base_url = t("model.baseUrl.required");
    else if (!URL_PATTERN.test(llmForm.base_url.trim())) errors.base_url = t("model.baseUrl.invalid");
    if (!llmForm.model.trim()) errors.model = t("model.model.required");
    const maxTokens = Number(llmForm.max_tokens);
    if (
      !Number.isInteger(maxTokens) ||
      maxTokens < MAX_TOKENS_MIN ||
      maxTokens > MAX_TOKENS_MAX
    ) {
      errors.max_tokens = t("model.maxTokens.invalid");
    }
    if (llmForm.api_key && llmForm.api_key.trim().length < 8) {
      errors.api_key = t("model.apiKey.invalid");
    }
    return errors;
  };

  const validateEmbedding = (): FieldErrors => {
    const errors: FieldErrors = {};
    if (!embeddingForm.base_url.trim()) errors.base_url = t("embedding.baseUrl.required");
    else if (!URL_PATTERN.test(embeddingForm.base_url.trim())) {
      errors.base_url = t("embedding.baseUrl.invalid");
    }
    if (!embeddingForm.model.trim()) errors.model = t("embedding.model.required");
    if (embeddingForm.dim.trim()) {
      const dim = Number(embeddingForm.dim);
      if (!Number.isInteger(dim) || dim < 1 || dim > 8192) errors.dim = t("embedding.dim.invalid");
    }
    if (embeddingForm.api_key && embeddingForm.api_key.trim().length < 8) {
      errors.api_key = t("embedding.apiKey.invalid");
    }
    return errors;
  };

  const toastError = (prefix: string, error: unknown) => {
    const code = error instanceof ApiError ? error.code : "UNKNOWN";
    toastApi.push({
      kind: "error",
      message: `${prefix}：${errorMessage(code)}`,
      hint: error instanceof ApiError ? error.hint ?? errorAction(code) : undefined,
    });
  };

  const handleSaveLlm = async () => {
    const errors = validateLlm();
    setLlmErrors(errors);
    if (Object.keys(errors).length > 0) return;

    const llm: LLMConfigWrite = {
      base_url: llmForm.base_url.trim().replace(/\/+$/, ""),
      model: llmForm.model.trim(),
      temperature: llmForm.temperature,
      max_tokens: Number(llmForm.max_tokens),
    };
    if (llmForm.api_key.trim()) llm.api_key = llmForm.api_key.trim();

    try {
      const updated = await saveSettings.mutateAsync({ llm });
      // 用返回值覆盖表单：服务端会做 `rstrip("/")`，不回填的话表单与服务端就不一致了。
      // `llmFormOf` 把 `api_key` 重置成空串——密钥只写不读，保存后输入框必须清空。
      setLlmForm(llmFormOf(updated));
      setLlmErrors({});
      toastApi.push({ kind: "success", message: t("model.saved") });
    } catch (error) {
      toastError(t("model.saveFailed"), error);
    }
  };

  const handleTestLlm = async () => {
    const override: LLMConfigWrite | null =
      llmForm.base_url.trim() && llmForm.model.trim()
        ? {
            base_url: llmForm.base_url.trim(),
            model: llmForm.model.trim(),
            temperature: llmForm.temperature,
            max_tokens: Number(llmForm.max_tokens) || 1024,
            ...(llmForm.api_key.trim() ? { api_key: llmForm.api_key.trim() } : {}),
          }
        : null;
    try {
      await testLlm.mutateAsync({ kind: "llm", llm: override });
    } catch {
      // 请求级失败由 TestResultLine 渲染 error
    }
  };

  const handleSaveEmbedding = async () => {
    const errors = validateEmbedding();
    setEmbeddingErrors(errors);
    if (Object.keys(errors).length > 0) return;

    const embedding: EmbeddingConfigWrite = {
      base_url: embeddingForm.base_url.trim().replace(/\/+$/, ""),
      model: embeddingForm.model.trim(),
    };
    if (embeddingForm.dim.trim()) embedding.dim = Number(embeddingForm.dim);
    if (embeddingForm.api_key.trim()) embedding.api_key = embeddingForm.api_key.trim();

    try {
      const updated = await saveSettings.mutateAsync({ embedding });
      setEmbeddingForm(embeddingFormOf(updated));
      setEmbeddingErrors({});
      toastApi.push({ kind: "success", message: t("embedding.saved") });
    } catch (error) {
      toastError(t("embedding.saveFailed"), error);
    }
  };

  const handleTestEmbedding = async () => {
    const override: EmbeddingConfigWrite | null =
      embeddingForm.base_url.trim() && embeddingForm.model.trim()
        ? {
            base_url: embeddingForm.base_url.trim(),
            model: embeddingForm.model.trim(),
            ...(embeddingForm.dim.trim() ? { dim: Number(embeddingForm.dim) } : {}),
            ...(embeddingForm.api_key.trim() ? { api_key: embeddingForm.api_key.trim() } : {}),
          }
        : null;
    try {
      await testEmbedding.mutateAsync({ kind: "embedding", embedding: override });
    } catch {
      // 同上
    }
  };

  const handleSavePrivate = async () => {
    const value = privateDir.trim();
    if (value && !ABS_PATH_PATTERN.test(value)) {
      setPrivateError(t("private.dir.invalid"));
      return;
    }
    setPrivateError(null);
    try {
      const updated = await saveSettings.mutateAsync({ private_dir: value ? value : null });
      setPrivateDir(updated.private_dir ?? "");
      toastApi.push({ kind: "success", message: t("private.saved") });
    } catch (error) {
      if (error instanceof ApiError && (error.code === "PRIVATE_DIR_INVALID" || error.code === "PATH_FORBIDDEN")) {
        // 行内提示（§9.6 Error 的「错误码 + 人话」），不弹 Toast 抢焦点
        setPrivateError(`${errorMessage(error.code)}（${error.code}）${error.hint ? ` · ${error.hint}` : ""}`);
      } else {
        toastError(t("private.saveFailed"), error);
      }
    }
  };

  const handleRescan = async () => {
    setPrivateError(null);
    try {
      const report = await privateScan.mutateAsync();
      setScanReport(report);
      toastApi.push({
        kind: "success",
        message: t("private.scanned", { added: report.added, updated: report.updated }),
      });
    } catch (error) {
      if (error instanceof ApiError && (error.code === "PRIVATE_DIR_INVALID" || error.code === "PATH_FORBIDDEN")) {
        setPrivateError(`${errorMessage(error.code)}（${error.code}）${error.hint ? ` · ${error.hint}` : ""}`);
      } else {
        toastError(t("private.scanFailed"), error);
      }
    }
  };

  const handleRebuild = async (mode: IndexMode) => {
    if (retryLeft > 0) return;
    setRebuildMode(mode);
    try {
      const result = await rebuildMutation.mutateAsync({ mode });
      completedTask.current = null;
      setTaskId(result.task_id);
      setIndexStarted(true);
      toastApi.push({ kind: "success", message: t("index.started") });
    } catch (error) {
      if (error instanceof ApiError && error.code === "INDEX_BUILDING") {
        const runningId = readTaskId(error);
        completedTask.current = null;
        if (runningId) {
          setTaskId(runningId);
          setIndexStarted(true);
        }
        toastApi.push({ kind: "warn", message: t("index.building") });
      } else if (error instanceof ApiError && error.code === "RATE_LIMITED") {
        const seconds = readRetryAfter(error);
        setRetryUntil(Date.now() + seconds * 1_000);
        setNowTick(Date.now());
        toastApi.push({ kind: "warn", message: t("index.rateLimited", { seconds }) });
      } else {
        toastError(t("index.failedToast"), error);
      }
    } finally {
      setRebuildMode(null);
    }
  };

  const handleLanguage = async (value: string) => {
    const next = value as Locale;
    const previous = (data?.language ?? i18n.language) as Locale;
    setLocale(next); // 即时生效（i18n + localStorage），再落库
    setLanguageBusy(true);
    try {
      await saveSettings.mutateAsync({ language: next });
      toastApi.push({ kind: "success", message: t("general.language.saved") });
    } catch (error) {
      setLocale(previous);
      toastError(t("general.language.failed"), error);
    } finally {
      setLanguageBusy(false);
    }
  };

  const handleRunner = async (next: boolean) => {
    const previous = data?.code_runner_enabled ?? true;
    setRunnerBusy(true);
    syncRunnerFlag(next); // 先让算法页立刻响应，保存失败再回滚
    try {
      const updated = await saveSettings.mutateAsync({ code_runner_enabled: next });
      syncRunnerFlag(updated.code_runner_enabled);
      toastApi.push({ kind: "success", message: t("general.runner.saved") });
    } catch (error) {
      syncRunnerFlag(previous);
      toastError(t("general.runner.failed"), error);
    } finally {
      setRunnerBusy(false);
    }
  };

  /* ------------------------------------------------------------ 渲染 */

  if (settings.isLoading) {
    return (
      <section className="page page-enter settings-page" aria-busy="true">
        <header className="settings-head">
          <h1 className="page__title">{t("title")}</h1>
          <p className="page__status">{slow ? t("state.loadingSlow") : t("state.loading")}</p>
        </header>
        {[0, 1, 2, 3].map((index) => (
          <div className="settings-section settings-section--skeleton" key={index}>
            <Skeleton width="180px" height={20} />
            <Skeleton width="60%" height={14} />
            <Skeleton width="100%" height={38} />
            <Skeleton width="100%" height={38} />
          </div>
        ))}
      </section>
    );
  }

  if (settings.isError || !data) {
    return (
      <section className="page page-enter settings-page">
        <header className="settings-head">
          <h1 className="page__title">{t("title")}</h1>
        </header>
        <ErrorState
          title={t("error.loadTitle")}
          code={settings.error instanceof ApiError ? settings.error.code : "INTERNAL"}
          detail={settings.error}
          requestId={settings.error instanceof ApiError ? settings.error.requestId : undefined}
          onRetry={() => void settings.refetch()}
          retrying={settings.isFetching}
          retryLabel={t("common:action.retry")}
          copyLabel={t("common:error.copyDetail")}
          copiedLabel={t("common:action.copied")}
        />
      </section>
    );
  }

  const offline = health.isError || (health.data !== undefined && !health.data.db_ready);
  const llmConfigured = data.llm_configured;
  const embeddingConfigured = data.embedding_configured;
  const rebuildDisabled = retryLeft > 0 || status?.state === "running" || rebuildMode !== null;
  const port = window.location.port || (window.location.protocol === "https:" ? "443" : "80");

  return (
    <TooltipProvider>
      <section className="page page-enter settings-page">
        <header className="settings-head">
          <div>
            <h1 className="page__title">{t("title")}</h1>
            <p className="page__status">{t("subtitle")}</p>
          </div>
          {anyDirty ? (
            <Badge tone="accent" size="sm">
              {t("unsaved")}
            </Badge>
          ) : null}
        </header>

        {offline ? (
          <InlineNotice tone="warn" title={t("error.offlineTitle")}>
            <p>{t("error.offline")}</p>
          </InlineNotice>
        ) : null}

        {/* ------------------------------------------------------ 模型 */}
        <SettingsSection
          id={`${formId}-model`}
          title={t("model.title")}
          description={t("model.description")}
          status={
            <div className="settings-section__badges">
              <StateBadge
                on={llmConfigured}
                onLabel={t("state.configured")}
                offLabel={t("state.unconfigured")}
              />
              <Badge tone={data.secret_backend === "keyring" ? "primary" : "danger"} size="sm">
                {data.secret_backend === "keyring"
                  ? t("secret.keyring")
                  : t("secret.file")}
              </Badge>
            </div>
          }
          footer={
            <div className="settings-actions">
              <Tooltip content={offline ? t("error.offline") : t("model.testHint")}>
                <span className="settings-tooltip-anchor">
                  <Button
                    variant="secondary"
                    iconLeft={<Gauge size={15} aria-hidden="true" />}
                    loading={testLlm.isPending}
                    disabled={offline}
                    onClick={() => void handleTestLlm()}
                  >
                    {testLlm.isPending ? t("test.running") : t("test.action")}
                  </Button>
                </span>
              </Tooltip>
              <Button
                variant="primary"
                iconLeft={<Save size={15} aria-hidden="true" />}
                loading={saveSettings.isPending && saveSettings.variables?.llm !== undefined}
                onClick={() => void handleSaveLlm()}
              >
                {t("model.save")}
              </Button>
            </div>
          }
        >
          <SettingRow
            label={t("model.baseUrl.label")}
            htmlFor={`${formId}-llm-base-url`}
            hint={t("model.baseUrl.hint")}
          >
            <Input
              id={`${formId}-llm-base-url`}
              value={llmForm.base_url}
              placeholder="https://api.deepseek.com/v1"
              inputMode="url"
              spellCheck={false}
              autoComplete="off"
              error={llmErrors.base_url}
              onChange={(event) =>
                setLlmForm((current) => ({ ...current, base_url: event.target.value }))
              }
            />
          </SettingRow>

          <SettingRow
            label={t("model.model.label")}
            htmlFor={`${formId}-llm-model`}
            hint={t("model.model.hint")}
          >
            <Input
              id={`${formId}-llm-model`}
              value={llmForm.model}
              placeholder="deepseek-chat"
              spellCheck={false}
              autoComplete="off"
              error={llmErrors.model}
              onChange={(event) =>
                setLlmForm((current) => ({ ...current, model: event.target.value }))
              }
            />
          </SettingRow>

          <SettingRow
            label={t("model.apiKey.label")}
            htmlFor={`${formId}-llm-api-key`}
            hint={t("model.apiKey.hint")}
            note={
              data.llm.api_key_masked
                ? t("model.apiKey.current", { masked: data.llm.api_key_masked })
                : t("model.apiKey.none")
            }
          >
            <Input
              id={`${formId}-llm-api-key`}
              type="password"
              value={llmForm.api_key}
              placeholder={data.llm.api_key_masked ?? t("model.apiKey.placeholder")}
              autoComplete="new-password"
              spellCheck={false}
              iconLeft={<KeyRound size={14} aria-hidden="true" />}
              error={llmErrors.api_key}
              onChange={(event) =>
                setLlmForm((current) => ({ ...current, api_key: event.target.value }))
              }
            />
          </SettingRow>

          <SettingRow
            label={t("model.temperature.label")}
            hint={t("model.temperature.hint")}
          >
            <Slider
              value={llmForm.temperature}
              min={0}
              max={2}
              step={0.1}
              ariaLabel={t("model.temperature.label")}
              valueLabel={llmForm.temperature.toFixed(1)}
              onValueChange={(values) =>
                setLlmForm((current) => ({ ...current, temperature: values[0] ?? current.temperature }))
              }
            />
          </SettingRow>

          <SettingRow
            label={t("model.maxTokens.label")}
            htmlFor={`${formId}-llm-max-tokens`}
            hint={t("model.maxTokens.hint")}
          >
            <Input
              id={`${formId}-llm-max-tokens`}
              type="number"
              min={MAX_TOKENS_MIN}
              max={MAX_TOKENS_MAX}
              step={1}
              value={llmForm.max_tokens}
              error={llmErrors.max_tokens}
              onChange={(event) =>
                setLlmForm((current) => ({ ...current, max_tokens: event.target.value }))
              }
            />
          </SettingRow>

          <TestResultLine
            result={testLlm.variables?.kind === "llm" ? (testLlm.data ?? null) : null}
            error={testLlm.variables?.kind === "llm" ? testLlm.error : null}
            pending={testLlm.isPending}
          />
        </SettingsSection>

        {/* -------------------------------------------------- 语义检索 */}
        <SettingsSection
          id={`${formId}-embedding`}
          title={t("embedding.title")}
          description={t("embedding.description")}
          status={
            <StateBadge
              on={embeddingConfigured}
              onLabel={t("state.enabled")}
              offLabel={t("state.disabled")}
            />
          }
          footer={
            <div className="settings-actions">
              <Tooltip content={offline ? t("error.offline") : t("embedding.testHint")}>
                <span className="settings-tooltip-anchor">
                  <Button
                    variant="secondary"
                    iconLeft={<Gauge size={15} aria-hidden="true" />}
                    loading={testEmbedding.isPending}
                    disabled={offline}
                    onClick={() => void handleTestEmbedding()}
                  >
                    {testEmbedding.isPending ? t("test.running") : t("test.action")}
                  </Button>
                </span>
              </Tooltip>
              <Button
                variant="primary"
                iconLeft={<Save size={15} aria-hidden="true" />}
                loading={saveSettings.isPending && saveSettings.variables?.embedding !== undefined}
                onClick={() => void handleSaveEmbedding()}
              >
                {t("embedding.save")}
              </Button>
            </div>
          }
        >
          {!embeddingConfigured ? (
            <InlineNotice tone="warn" title={t("embedding.unconfigured.title")}>
              <p>{t("embedding.unconfigured.body")}</p>
            </InlineNotice>
          ) : null}

          <SettingRow
            label={t("embedding.baseUrl.label")}
            htmlFor={`${formId}-embedding-base-url`}
            hint={t("embedding.baseUrl.hint")}
          >
            <Input
              id={`${formId}-embedding-base-url`}
              value={embeddingForm.base_url}
              placeholder="https://api.deepseek.com/v1"
              inputMode="url"
              spellCheck={false}
              autoComplete="off"
              error={embeddingErrors.base_url}
              onChange={(event) =>
                setEmbeddingForm((current) => ({ ...current, base_url: event.target.value }))
              }
            />
          </SettingRow>

          <SettingRow
            label={t("embedding.model.label")}
            htmlFor={`${formId}-embedding-model`}
            hint={t("embedding.model.hint")}
          >
            <Input
              id={`${formId}-embedding-model`}
              value={embeddingForm.model}
              placeholder="text-embedding-3-small"
              spellCheck={false}
              autoComplete="off"
              error={embeddingErrors.model}
              onChange={(event) =>
                setEmbeddingForm((current) => ({ ...current, model: event.target.value }))
              }
            />
          </SettingRow>

          <SettingRow
            label={t("embedding.dim.label")}
            htmlFor={`${formId}-embedding-dim`}
            hint={t("embedding.dim.hint")}
          >
            <Input
              id={`${formId}-embedding-dim`}
              type="number"
              min={1}
              max={8192}
              step={1}
              value={embeddingForm.dim}
              placeholder={t("embedding.dim.placeholder")}
              error={embeddingErrors.dim}
              onChange={(event) =>
                setEmbeddingForm((current) => ({ ...current, dim: event.target.value }))
              }
            />
          </SettingRow>

          <SettingRow
            label={t("embedding.apiKey.label")}
            htmlFor={`${formId}-embedding-api-key`}
            hint={t("embedding.apiKey.hint")}
            note={
              data.embedding.api_key_masked
                ? t("model.apiKey.current", { masked: data.embedding.api_key_masked })
                : t("model.apiKey.none")
            }
          >
            <Input
              id={`${formId}-embedding-api-key`}
              type="password"
              value={embeddingForm.api_key}
              placeholder={data.embedding.api_key_masked ?? t("model.apiKey.placeholder")}
              autoComplete="new-password"
              spellCheck={false}
              iconLeft={<KeyRound size={14} aria-hidden="true" />}
              error={embeddingErrors.api_key}
              onChange={(event) =>
                setEmbeddingForm((current) => ({ ...current, api_key: event.target.value }))
              }
            />
          </SettingRow>

          <TestResultLine
            result={testEmbedding.variables?.kind === "embedding" ? (testEmbedding.data ?? null) : null}
            error={testEmbedding.variables?.kind === "embedding" ? testEmbedding.error : null}
            pending={testEmbedding.isPending}
          />

          <div className="settings-subsection">
            <h3 className="settings-subsection__title">
              <Database size={15} aria-hidden="true" />
              {t("index.title")}
            </h3>
            <p className="settings-subsection__desc">{t("index.description")}</p>

            <div className="settings-actions">
              <Button
                variant="secondary"
                iconLeft={<RefreshCw size={15} aria-hidden="true" />}
                loading={rebuildMode === "full"}
                disabled={rebuildDisabled}
                onClick={() => void handleRebuild("full")}
              >
                {t("index.full")}
              </Button>
              <Button
                variant="ghost"
                iconLeft={<Play size={15} aria-hidden="true" />}
                loading={rebuildMode === "incremental"}
                disabled={rebuildDisabled}
                onClick={() => void handleRebuild("incremental")}
              >
                {t("index.incremental")}
              </Button>
            </div>

            {retryLeft > 0 ? (
              <InlineNotice tone="warn" title={t("index.rateLimited", { seconds: retryLeft })}>
                <p>{t("index.rateLimitedHint")}</p>
              </InlineNotice>
            ) : null}

            <p className="settings-subsection__note">{t("index.note")}</p>

            <IndexProgress status={status} started={indexStarted} />

            {status?.last_indexed_at ? (
              <p className="settings-index__meta">
                {t("index.lastIndexed", { time: formatTime(status.last_indexed_at) })}
                {status.content_version ? (
                  <code className="settings-index__version">{status.content_version.slice(0, 12)}</code>
                ) : null}
              </p>
            ) : null}
          </div>
        </SettingsSection>

        {/* -------------------------------------------------- 私有资料 */}
        <SettingsSection
          id={`${formId}-private`}
          title={t("private.title")}
          description={t("private.description")}
          status={
            <StateBadge
              on={!!data.private_dir}
              onLabel={t("private.custom")}
              offLabel={t("private.defaultDir")}
            />
          }
          footer={
            <div className="settings-actions">
              <Button
                variant="secondary"
                iconLeft={<RotateCcw size={15} aria-hidden="true" />}
                loading={privateScan.isPending}
                onClick={() => void handleRescan()}
              >
                {privateScan.isPending ? t("private.scanning") : t("private.rescan")}
              </Button>
              <Button
                variant="primary"
                iconLeft={<Save size={15} aria-hidden="true" />}
                loading={saveSettings.isPending && saveSettings.variables?.private_dir !== undefined}
                onClick={() => void handleSavePrivate()}
              >
                {t("private.save")}
              </Button>
            </div>
          }
        >
          <SettingRow
            label={t("private.dir.label")}
            htmlFor={`${formId}-private-dir`}
            hint={t("private.dir.hint")}
          >
            <Input
              id={`${formId}-private-dir`}
              value={privateDir}
              placeholder={t("private.dir.placeholder")}
              spellCheck={false}
              autoComplete="off"
              iconLeft={<FolderOpen size={14} aria-hidden="true" />}
              error={privateError}
              onChange={(event) => {
                setPrivateDir(event.target.value);
                setPrivateError(null);
              }}
            />
          </SettingRow>

          {scanReport ? (
            <ScanResult report={scanReport} />
          ) : (
            <p className="settings-empty" role="status">
              {t("private.result.empty")}
            </p>
          )}
        </SettingsSection>

        {/* ------------------------------------------------------ 通用 */}
        <SettingsSection
          id={`${formId}-general`}
          title={t("general.title")}
          description={t("general.description")}
        >
          <SettingRow
            label={t("general.language.label")}
            htmlFor={`${formId}-language`}
            hint={t("general.language.hint")}
            note={
              languageBusy ? (
                <span className="settings-inline-pending">{t("general.language.saving")}</span>
              ) : null
            }
          >
            <Select
              id={`${formId}-language`}
              value={data.language}
              disabled={languageBusy}
              onValueChange={(value) => void handleLanguage(value)}
              options={[
                { value: "zh-CN", label: t("general.language.zh") },
                { value: "en-US", label: t("general.language.en") },
              ]}
            />
          </SettingRow>

          <SettingRow
            label={t("general.port.label")}
            htmlFor={`${formId}-port`}
            hint={t("general.port.hint")}
            note={
              <span className="settings-inline-pending">
                <AlertTriangle size={12} aria-hidden="true" /> {t("general.port.restart")}
              </span>
            }
          >
            <Input
              id={`${formId}-port`}
              value={port}
              readOnly
              disabled
              className="tnum"
            />
          </SettingRow>

          <SettingRow
            label={t("general.runner.label")}
            hint={t("general.runner.hint")}
          >
            <Switch
              checked={data.code_runner_enabled}
              disabled={runnerBusy}
              ariaLabel={t("general.runner.label")}
              onCheckedChange={(checked) => void handleRunner(checked)}
            />
          </SettingRow>

          <SettingRow
            label={t("general.reset.label")}
            hint={t("general.reset.hint")}
          >
            <Button
              variant="danger"
              disabled
              iconLeft={<Terminal size={15} aria-hidden="true" />}
            >
              {t("general.reset.action")}
            </Button>
            <p className="settings-row__note settings-row__note--muted">
              {t("general.reset.unavailable")}
            </p>
          </SettingRow>
        </SettingsSection>

        {/* 语义检索不可用时的全局说明（§9.6 Partial 的兜底：本页就是"顶部提示条"） */}
        {!embeddingConfigured ? (
          <InlineNotice tone="info" title={t("partial.title")}>
            <p>{t("partial.body")}</p>
          </InlineNotice>
        ) : null}

        <p className="settings-foot">
          <Languages size={13} aria-hidden="true" />
          {t("footer", { backend: data.secret_backend })}
        </p>

      </section>
    </TooltipProvider>
  );
}
