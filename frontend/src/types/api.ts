/**
 * pgassistant 前端类型 —— **唯一类型来源**。
 *
 * 与 `backend/app/schemas/models.py` 逐字段一一对应；改一处必须同时改另一处与
 * `docs/contracts/API.md`（契约冻结规则见 `docs/contracts/DECISIONS.md`）。
 *
 * 三条约束：
 * 1. 纯类型文件：不 import 任何模块，不产生运行时代码（只有 type / interface）。
 * 2. 其它模块只能 `import type { … } from "@/types/api"`，**禁止复制粘贴**字段定义。
 * 3. 可选（`?`）= 服务端当前可能不返回（M1 现状或历史响应）；`| null` = 服务端会返回
 *    JSON null。两者语义不同，不要互相替换。
 *
 * 字段顺序与 Pydantic 模型保持一致，便于逐行对照。
 */

/* ------------------------------------------------------------------ 枚举 */

/** 8 类封闭意图（§7.3）。新增必须改需求文档本身。 */
export type Intent =
  | "GREETING"
  | "CAPABILITY"
  | "RESOURCE_SEARCH"
  | "PLAN_CREATE"
  | "PLAN_ADJUST"
  | "ADVICE"
  | "CLARIFY"
  | "OUT_OF_SCOPE";

export type Level = "beginner" | "intermediate" | "advanced";

/** 8 类资料类型（§4.6，DB 层 CHECK 约束）。 */
export type ResourceType =
  | "article"
  | "video"
  | "course"
  | "book"
  | "docs"
  | "exercise"
  | "summary"
  | "overview";

/** 共享内容包 vs 私有资料（§4.4）。 */
export type ResourceSource = "repo" | "private";

export type PlanStatus = "active" | "archived";
export type PlanOrigin = "agent" | "manual" | "template";

/** 条目三态；skipped 不计入进度分母（§8.4）。 */
export type PlanItemStatus = "todo" | "done" | "skipped";

/** 检索降级模式（与 `services/search.py` 的返回值逐字对齐）。 */
export type SearchMode =
  | "FIELD"
  | "AND"
  | "AND+CORE"
  | "OR"
  | "SINGLE"
  | "miss"
  | "empty"
  | "error";

export type Locale = "zh-CN" | "en-US";

/** SSE 事件名（封闭集合，§11 只给了这四个）。 */
export type SseEventName = "intent" | "delta" | "done" | "error";

/** `done` 携带的结果类型。 */
export type DoneKind = "text" | "search" | "plan_draft" | "clarify";
export type FinishReason = "stop" | "length" | "aborted";
export type RouteSource = "rule" | "llm" | "fallback";
export type SecretBackend = "keyring" | "file";
export type IndexRunState = "idle" | "running" | "failed";
export type IndexMode = "full" | "incremental";
export type PreferType = "video" | "docs" | "any";
export type SettingsTestKind = "llm" | "embedding";

/** 查询参数枚举（`GET /api/resources?sort=`）。 */
export type ResourceSort = "default" | "title" | "duration_min" | "updated_at";
/** 查询参数枚举（`GET /api/plans?status=`）。 */
export type PlanStatusFilter = "active" | "archived" | "all";

/** UTC ISO8601 字符串，例 `2026-10-30T11:27:13+00:00`。 */
export type Timestamp = string;
/** 资料对外主键，可含 `/`（§5.1）。 */
export type Slug = string;

/* ------------------------------------------------------------- 错误信封 */

/** 统一错误信封（§11）。**按 code 分支**，message 只展示。 */
export interface ErrorEnvelope {
  code: string;
  message: string;
  detail?: unknown;
  hint?: string | null;
  /** 仅 5xx 保证携带（M-1 尚未实现，前端不得假设存在）。 */
  request_id?: string | null;
}

/* --------------------------------------------------------------- 健康 */

export interface IndexState {
  files: number;
  indexed_at: Timestamp | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  db_ready: boolean;
  schema_version: number | null;
  version: string;
  index: IndexState | null;
  /** M4/M5 之后才有；缺席按 false 处理。 */
  model_configured?: boolean;
  embedding_configured?: boolean;
  index_version?: string | null;
}

/* --------------------------------------------------------------- 分类树 */

export interface LocalizedLabel {
  zh: string;
  en: string;
}

export interface CategoryNode {
  category: string;
  count: number;
}

export interface StackNode {
  stack: string;
  total: number;
  categories: CategoryNode[];
  /** M1 恒为 null/缺席；M0 建 `content/_meta/taxonomy.yml` 后补。 */
  label?: LocalizedLabel | null;
}

export interface TaxonomyResponse {
  stacks: StackNode[];
  levels: Level[];
  types: ResourceType[];
  total: number;
}

/* ----------------------------------------------------------------- 资料 */

/** 列表/搜索结果里的一条资料。 */
export interface ResourceSummary {
  /** 内部自增主键，**前端不得使用**（§5.1）；列表接口不返回，仅详情返回（D2 / D25）。 */
  id?: number | null;
  slug: Slug;
  title: string;
  summary: string;
  stack: string;
  category: string;
  level: Level;
  type: ResourceType;
  duration_min: number | null;
  author: string;
  license: string;
  source_url: string | null;
  /** 列表接口当前不返回，详情返回。 */
  url?: string | null;
  /**
   * 封面图的 content 根相对路径（可为 null）。
   * 可访问 URL = `/media/{cover_path}`；空值表示没有封面，**不要**自己拼前缀（D24）。
   */
  cover_path?: string | null;
  /** 列表接口当前不返回；详情返回。 */
  tags?: string[];
  /** 可站内运行的语言（§20.1）；契约是数组（M1 列表给的是逗号串，M2 修正）。 */
  runnable: string[] | null;
  lang: string;
  source?: ResourceSource;
}

export interface TocNode {
  level: number;
  text: string;
  anchor: string;
}

export interface ResourceRef {
  slug: Slug;
  title: string;
}

export interface ResourceDetail extends ResourceSummary {
  body_md: string;
  file_path: string;
  content_hash: string;
  is_deleted: 0 | 1;
  created_at: Timestamp | null;
  updated_at: Timestamp | null;
  /** M2 补。 */
  toc?: TocNode[];
  prev?: ResourceRef | null;
  next?: ResourceRef | null;
  /**
   * 正文媒体的基目录（content 根相对路径，如 `frontend/react`）；M2 起必返，M1 响应没有该字段。
   * 正文里的相对 `src` 解析为 `/media/{media_base}/{src}`；空串表示按 content 根解析（D24）。
   */
  media_base?: string;
}

export interface ResourceListResponse {
  items: ResourceSummary[];
  total: number;
  page: number;
  size: number;
  took_ms?: number | null;
  index_version?: string | null;
}

export interface RelatedResponse {
  items: ResourceSummary[];
  total: number;
  semantic_available: boolean;
}

/** `GET /api/resources` 的查询参数。 */
export interface ResourceListQuery {
  stack?: string;
  category?: string;
  level?: Level;
  type?: ResourceType;
  source?: ResourceSource;
  q?: string;
  page?: number;
  size?: number;
  sort?: ResourceSort;
}

/* ----------------------------------------------------------------- 检索 */

export interface SearchHit {
  slug: Slug;
  title: string;
  /** 命中词用 `[词]` 包裹的纯文本片段（**不是 HTML**，§13.2-2）。 */
  snippet: string;
  stack: string;
  category: string;
  level: Level;
  type: ResourceType;
  duration_min: number | null;
  author: string;
  license: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  tokens: string[];
  total: number;
  items: SearchHit[];
  took_ms?: number | null;
  /** null = 本次没走向量检索；false = 配了但不可用（§7.9 提示条）。 */
  semantic_available?: boolean | null;
  notice?: string | null;
}

/** `POST /api/search` 的请求体（与 GET 同语义）。 */
export interface SearchRequest {
  q: string;
  stack?: string | null;
  category?: string | null;
  level?: Level | null;
  type?: ResourceType | null;
  limit?: number;
  include_overview?: boolean;
}

/** `GET /api/search` 的查询参数。 */
export interface SearchQuery {
  q: string;
  stack?: string;
  category?: string;
  level?: Level;
  type?: ResourceType;
  limit?: number;
  include_overview?: boolean;
}

/* ------------------------------------------------------------- 计划草稿 */

export interface ItemDraft {
  title: string;
  /** null = 自定义任务（§7.6）。 */
  resource_slug?: Slug | null;
  est_minutes?: number;
  note?: string;
}

export interface StageDraft {
  title: string;
  weeks: number;
  description?: string;
  items: ItemDraft[];
}

/** 唯一事实来源：模板 YAML、AI 产出、落库入参共用（§7.6）。 */
export interface PlanDraft {
  title: string;
  goal: string;
  weeks: number;
  weekly_hours: number;
  stages: StageDraft[];
}

/** L2 抽取出的槽位（§7.5），字段全可空。 */
export interface PlanSlots {
  goal?: string | null;
  weeks?: number | null;
  weekly_hours?: number | null;
  level?: Level | null;
  stack?: string | null;
}

/* ----------------------------------------------------------------- 计划 */

export interface StageProgress {
  stage_id: number;
  done: number;
  todo: number;
  skipped: number;
  percent: number;
  est_minutes: number;
}

export interface PlanProgress {
  percent: number;
  done_items: number;
  todo_items: number;
  skipped_items: number;
  total_minutes: number;
  invested_minutes: number;
  remaining_weeks: number;
  stages: StageProgress[];
}

export interface ItemRead {
  id: number;
  stage_id: number;
  /** 保存时的标题快照（§8.4）。 */
  title: string;
  resource_slug: Slug | null;
  note: string;
  est_minutes: number;
  order_index: number;
  status: PlanItemStatus;
  /** 二期节点图预留（§8.5）。 */
  depends_on: number[] | null;
  completed_at: Timestamp | null;
  version: number;
  /** 软删除标记（schema v2）：正常查询恒为 0，已删除的条目不出现在响应里。 */
  is_deleted: 0 | 1;
  /** 按 slug 实时查到的标题；null = 资料已下架（回退快照并标灰）。 */
  resolved_title: string | null;
  resource_available: boolean;
  /**
   * 按 `resource_slug` 实时解析的资料类型（§8.5 条目行类型图标 /「只看视频」筛选，D25）。
   * null = 未绑定资料或资料已下架。
   * 现状：键已随 Pydantic 模型输出，但解析未落地，**值恒为 null**（计划模块待办）；
   * 所以先写可选，且**不得**把 null 当"无类型"的权威判断。落地后由契约层收紧为必填。
   */
  resource_type?: ResourceType | null;
  /** 按 `resource_slug` 实时解析的难度（§8.5 难度徽章，D25）；null 语义同 `resource_type`。 */
  resource_level?: Level | null;
}

export interface StageRead {
  id: number;
  plan_id: number;
  title: string;
  description: string;
  weeks: number;
  order_index: number;
  items: ItemRead[];
  progress: StageProgress | null;
}

export interface PlanRead {
  id: number;
  title: string;
  goal: string;
  start_level: Level;
  weekly_hours: number;
  horizon_weeks: number;
  status: PlanStatus;
  origin: PlanOrigin;
  /** 乐观锁令牌（D4）。 */
  version: number;
  created_at: Timestamp | null;
  updated_at: Timestamp | null;
  /** 软删除标记（schema v2）：0 = 正常，1 = 已删除；GET 只返回 0。 */
  is_deleted: 0 | 1;
  stages: StageRead[];
  progress: PlanProgress | null;
}

export interface PlanSummary {
  id: number;
  title: string;
  goal: string;
  start_level: Level;
  weekly_hours: number;
  horizon_weeks: number;
  status: PlanStatus;
  origin: PlanOrigin;
  version: number;
  updated_at: Timestamp | null;
  /** 列表只返回未删除的计划，恒为 0。 */
  is_deleted: 0 | 1;
  progress: PlanProgress | null;
}

export interface PlanListResponse {
  items: PlanSummary[];
  total: number;
  active_plan_id: number | null;
}

/** `POST /api/plans`（创建类请求不带 version）。 */
export interface PlanCreate {
  draft: PlanDraft;
  origin?: PlanOrigin;
  template_id?: string | null;
  start_level?: Level;
  set_active?: boolean;
}

export interface PlanPatch {
  title?: string | null;
  goal?: string | null;
  status?: PlanStatus | null;
  set_active?: boolean | null;
  version: number;
}

/** 阶段写操作的 version 是**所属计划**的（D4）。 */
export interface StageCreate {
  title: string;
  description?: string;
  weeks?: number;
  order_index?: number | null;
  version: number;
}

export interface StagePatch {
  title?: string | null;
  description?: string | null;
  weeks?: number | null;
  order_index?: number | null;
  version: number;
}

export interface ItemCreate {
  stage_id: number;
  title: string;
  resource_slug?: Slug | null;
  note?: string;
  est_minutes?: number;
  order_index?: number | null;
  version: number;
}

export interface ItemPatch {
  title?: string | null;
  note?: string | null;
  est_minutes?: number | null;
  status?: PlanItemStatus | null;
  stage_id?: number | null;
  /** 显式 `null` = 解绑；字段缺席 = 不改。 */
  resource_slug?: Slug | null;
  order_index?: number | null;
  version: number;
}

/** 计划树子资源的写操作统一响应（D6）：带回整棵更新后的计划。 */
export interface PlanMutationResult {
  plan: PlanRead;
  affected_stage_ids: number[];
  affected_item_ids: number[];
}

export interface PlanTemplateSummary {
  template_id: string;
  title: string;
  goal: string;
  weeks: number;
  weekly_hours: number;
  stage_count: number;
  item_count: number;
  label: LocalizedLabel | null;
}

export interface PlanTemplateListResponse {
  items: PlanTemplateSummary[];
  total: number;
}

export interface PlanListQuery {
  status?: PlanStatusFilter;
}

/* ----------------------------------------------------------------- 助手 */

export interface PageContext {
  resource_slug?: Slug | null;
  resource_title?: string | null;
  plan_id?: number | null;
}

export interface AgentExtractRequest {
  text: string;
  locale?: Locale;
  page_context?: PageContext | null;
}

export interface AgentExtractResponse {
  intent: Intent;
  confidence: number;
  slots: PlanSlots;
  needs_clarify: boolean;
  clarify_options: string[];
}

export interface AgentPlanDraftRequest {
  goal: string;
  level?: Level;
  weekly_hours?: number;
  weeks?: number;
  prefer?: PreferType | null;
  stack?: string | null;
  template_id?: string | null;
}

export interface AgentChatRequest {
  /**
   * 非空即可，**不设长度上限**（D26）：>500 字由服务端 L0 截断后照常 200 / SSE，
   * 空串或全空白才 422。前端不要自行截断或提前报错。
   */
  message: string;
  locale?: Locale;
  page_context?: PageContext | null;
}

export interface TokenUsage {
  tokens_in: number;
  tokens_out: number;
  /** true = 上游没给 usage，前端显示的是估算值（§7.10）。 */
  estimated: boolean;
}

/* ------------------------------------------------------------- SSE 载荷 */

export interface SseIntentData {
  intent: Intent;
  confidence: number;
  source: RouteSource;
  slots?: PlanSlots | null;
  needs_clarify: boolean;
  clarify_options: string[];
}

export interface SseDeltaData {
  text: string;
}

export interface SseDoneData {
  kind: DoneKind;
  message_id: number | null;
  finish_reason: FinishReason;
  aborted: boolean;
  usage: TokenUsage;
  intent: Intent | null;
  /** `kind` 决定实际类型：search → SearchResponse；plan_draft → PlanDraft；其余 null。 */
  result: SearchResponse | PlanDraft | null;
}

/** `event: error` 的 data，与错误信封同形。 */
export type SseErrorData = ErrorEnvelope;

/** 帧样板：`event: <name>\ndata: <单行 JSON>\n\n`（仅用于测试断言与文档对照）。 */
export interface SseFrame {
  event: SseEventName;
  data: string;
}

/* ----------------------------------------------------------------- 设置 */

export interface LLMConfigRead {
  base_url: string | null;
  model: string | null;
  temperature: number;
  max_tokens: number;
  /** 永远只回掩码，如 `sk-****1234`（§13.1）。 */
  api_key_masked: string | null;
}

export interface EmbeddingConfigRead {
  base_url: string | null;
  model: string | null;
  dim: number | null;
  api_key_masked: string | null;
}

export interface LLMConfigWrite {
  base_url: string;
  model: string;
  /** 只写不读；不传表示保持原值。 */
  api_key?: string | null;
  temperature?: number;
  max_tokens?: number;
}

export interface EmbeddingConfigWrite {
  base_url: string;
  model: string;
  api_key?: string | null;
  dim?: number | null;
}

export interface SettingsRead {
  llm: LLMConfigRead;
  embedding: EmbeddingConfigRead;
  private_dir: string | null;
  language: Locale;
  code_runner_enabled: boolean;
  onboarded: boolean;
  /** 密钥实际存在哪一级（§13.8 要求 UI 明示）。 */
  secret_backend: SecretBackend;
  llm_configured: boolean;
  embedding_configured: boolean;
}

/** 分组可选：未提供的组保持原值（局部更新）。 */
export interface SettingsWrite {
  llm?: LLMConfigWrite | null;
  embedding?: EmbeddingConfigWrite | null;
  private_dir?: string | null;
  language?: Locale | null;
  code_runner_enabled?: boolean | null;
}

export interface SettingsTestRequest {
  kind: SettingsTestKind;
  /** 带上则用请求里的配置测，不落库。 */
  llm?: LLMConfigWrite | null;
  embedding?: EmbeddingConfigWrite | null;
}

export interface SettingsTestResponse {
  ok: boolean;
  kind: SettingsTestKind;
  latency_ms: number;
  model: string | null;
  message: string;
  error_code: string | null;
}

/* ------------------------------------------------------------ 索引 / 私有 */

export interface IndexStatus {
  state: IndexRunState;
  mode: IndexMode | null;
  task_id: string | null;
  files_total: number;
  files_done: number;
  percent: number;
  started_at: Timestamp | null;
  updated_at: Timestamp | null;
  last_indexed_at: Timestamp | null;
  content_version: string | null;
  error: string | null;
}

export interface IndexRebuildRequest {
  mode?: IndexMode;
}

export interface IndexRebuildResponse {
  task_id: string;
  accepted: boolean;
  status: IndexStatus;
}

/** 空对象（`extra="forbid"` 的空请求体）。 */
export type PrivateRescanRequest = Record<string, never>;

export interface PrivateRescanResponse {
  root: string;
  scanned: number;
  added: number;
  updated: number;
  removed: number;
  skipped: number;
  errors: string[];
  took_ms: number;
}
