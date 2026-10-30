"""契约模型（Pydantic v2）—— 全项目唯一的请求/响应体定义。

对应文档：
- `docs/contracts/API.md`（逐端点表格与示例，字段名/类型必须与本文件逐字一致）
- `docs/contracts/DB.md`（表结构与字段口径）
- `docs/contracts/DECISIONS.md`（冲突裁定；本文件的每个例外都能在那里找到理由）
- `frontend/src/types/api.ts`（逐字段对应的 TypeScript 类型）

三条硬规则：

1. **本模块是叶子**：只允许 `pydantic` + 标准库，禁止 import FastAPI、`app.db`、
   `app.services` 或任何路由模块，否则会绕出循环依赖。
2. **M1 已实现的接口必须能被这里的模型校验通过**（health / taxonomy / resources /
   search）。所以「新增字段」一律带默认值，历史响应缺少它们也能解析。
3. 改字段 = 改契约。必须同时改 API.md、api.ts、DECISIONS.md，并由契约层重新验收。
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------- 复用约束
# 时间统一存 UTC ISO8601（需求文档 §5）；indexer.now_iso() 产出
# "2026-10-30T11:27:13+00:00" 这种带偏移的形式。
ISO8601_UTC = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"

# slug 是资料对外主键（§5.1）：允许 / 分层；禁止首尾斜杠、连续斜杠、反斜杠、
# 空白、控制字符、? # %。
# 注意：pydantic v2 的正则走 Rust regex，**不支持 look-around**（也没有 (?!) (<?!)），
# 所以"禁止 .. 段"这一条放在 AfterValidator 里做，不用正则硬凑。
SLUG_PATTERN = r"^[^\s\x00-\x1f?#%\\]+(?:/[^\s\x00-\x1f?#%\\]+)*$"

# 分类/技术栈：共享内容必须注册在 content/_meta/taxonomy.yml（§4.6），
# 私有资料允许自定义值（§4.4），所以这里只禁空白与控制字符。
TOKEN_PATTERN = r"^[^\s\x00-\x1f]+$"

HTTP_URL_PATTERN = r"^https?://\S+$"


def _no_dot_segments(value: str) -> str:
    """挡住空段与 `.` / `..` 段（路径穿越）。"""
    for segment in value.split("/"):
        if segment in {"", ".", ".."}:
            raise ValueError("slug 不允许空段、'.' 段或 '..' 段（§5.1 防穿越）")
    return value


Timestamp = Annotated[
    str,
    Field(min_length=20, max_length=40, pattern=ISO8601_UTC, description="UTC ISO8601"),
]
Slug = Annotated[
    str,
    Field(min_length=1, max_length=200, pattern=SLUG_PATTERN, description="资料对外主键（§5.1）"),
    AfterValidator(_no_dot_segments),
]
TokenStr = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=TOKEN_PATTERN, description="分类/技术栈等短标识"),
]


class _Model(BaseModel):
    """响应模型：忽略未知字段（服务端加法式扩展不会打碎旧前端）。"""

    model_config = ConfigDict(extra="ignore", use_enum_values=True)


class _Request(BaseModel):
    """请求模型：拒绝未知字段（拼错的字段名必须 422，不能静默丢弃）。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ------------------------------------------------------------------- 枚举
class Intent(str, Enum):
    """8 类封闭意图（§7.3）。新增必须改需求文档本身，不是改这里。"""

    GREETING = "GREETING"
    CAPABILITY = "CAPABILITY"
    RESOURCE_SEARCH = "RESOURCE_SEARCH"
    PLAN_CREATE = "PLAN_CREATE"
    PLAN_ADJUST = "PLAN_ADJUST"
    ADVICE = "ADVICE"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Level(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class ResourceType(str, Enum):
    """8 类资料类型（§4.6 / §5.3，DB 里是 CHECK 约束）。"""

    ARTICLE = "article"
    VIDEO = "video"
    COURSE = "course"
    BOOK = "book"
    DOCS = "docs"
    EXERCISE = "exercise"
    SUMMARY = "summary"
    OVERVIEW = "overview"


class ResourceSource(str, Enum):
    """共享内容包 vs 用户私有资料（§4.4）。"""

    REPO = "repo"
    PRIVATE = "private"


class PlanStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class PlanOrigin(str, Enum):
    """计划来源。schema_v1 的 CHECK 已含 template（§8.6 模板路径）。"""

    AGENT = "agent"
    MANUAL = "manual"
    TEMPLATE = "template"


class PlanItemStatus(str, Enum):
    """条目三态；skipped 不计入分母（§8.4）。"""

    TODO = "todo"
    DONE = "done"
    SKIPPED = "skipped"


SearchMode = Literal["FIELD", "AND", "AND+CORE", "OR", "SINGLE", "miss", "empty", "error"]
"""检索降级模式（services/search.py 的返回值，逐字对齐）。

- FIELD：只在 title + tags 里命中
- AND / AND+CORE / OR：三级降级
- SINGLE：单 token 查询
- miss：词都对但没结果；empty：查询词被清空（多半是缺 jieba）
- error：FTS 语法或 SQL 异常
"""

Locale = Literal["zh-CN", "en-US"]
SSEEventName = Literal["intent", "delta", "done", "error"]
DoneKind = Literal["text", "search", "plan_draft", "clarify"]
FinishReason = Literal["stop", "length", "aborted"]
RouteSource = Literal["rule", "llm", "fallback"]
SecretBackend = Literal["keyring", "file"]
IndexRunState = Literal["idle", "running", "failed"]
IndexMode = Literal["full", "incremental"]
PreferType = Literal["video", "docs", "any"]
SettingsTestKind = Literal["llm", "embedding"]

# 查询参数里的枚举（不落在任何请求体模型上，但前端与文档需要同一份取值集合）
ResourceSort = Literal["default", "title", "duration_min", "updated_at"]
PlanStatusFilter = Literal["active", "archived", "all"]


# ------------------------------------------------------------------- 错误
class ErrorEnvelope(_Model):
    """统一错误信封（§11）。code 是前端唯一分支依据，message 只给人看。"""

    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    detail: Any | None = None
    hint: str | None = Field(default=None, max_length=500)
    request_id: str | None = Field(default=None, max_length=64)


# ------------------------------------------------------------------- 健康
class IndexState(_Model):
    """index_state 表的一行摘要（§6.1）。"""

    files: int = Field(ge=0)
    indexed_at: Timestamp | None = None


class HealthResponse(_Model):
    """GET /api/health。免令牌，只暴露「能不能用」，不暴露内容（§13.7 口径 1）。"""

    status: Literal["ok", "degraded"] = "ok"
    db_ready: bool
    schema_version: int | None = Field(default=None, ge=0)
    version: str = Field(min_length=1, max_length=32)
    index: IndexState | None = None
    # 加法式扩展（M-1 之后补，M1 响应里暂时没有，全部给默认值）
    model_configured: bool = False
    embedding_configured: bool = False
    index_version: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------- 分类树
class LocalizedLabel(_Model):
    zh: str = Field(min_length=1, max_length=40)
    en: str = Field(min_length=1, max_length=40)


class CategoryNode(_Model):
    category: TokenStr
    count: int = Field(ge=0)


class StackNode(_Model):
    stack: TokenStr
    total: int = Field(ge=0)
    categories: list[CategoryNode] = Field(default_factory=list)
    label: LocalizedLabel | None = None


class TaxonomyResponse(_Model):
    """GET /api/taxonomy。只返回库内真实存在的分类（§4.6 规则 0）。"""

    stacks: list[StackNode] = Field(default_factory=list)
    levels: list[Level] = Field(default_factory=list)
    types: list[ResourceType] = Field(default_factory=list)
    total: int = Field(ge=0)


# ----------------------------------------------------------------- 资料
def _split_runnable(value):
    """M1 的列表接口把 TEXT 列原样透出（"python,javascript"）。

    契约统一为 `list[str]`（§5.3 / §20.8 写的是 JSON 数组，实现对不上，
    裁定见 DECISIONS D7）。这里做一次兼容转换，让模型既吃 M1 现状也吃修正后的数组。
    """
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return value


class ResourceSummary(_Model):
    """列表/搜索结果里的一条资料（§11 GET /api/resources 的 items 元素）。"""

    id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "内部自增主键，**仅详情**（ResourceDetail）返回；列表/搜索响应不返回该键，"
            "前端一律不得使用（§5.1 / D2 / D25）"
        ),
    )
    slug: Slug
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(default="", max_length=200)
    stack: TokenStr
    category: TokenStr
    level: Level
    type: ResourceType
    duration_min: int | None = Field(default=None, ge=0, le=600)
    author: str = Field(default="", max_length=120)
    license: str = Field(default="", max_length=64)
    source_url: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=500)
    cover_path: str | None = Field(
        default=None,
        max_length=300,
        description="封面图的 content 根相对路径（可为 null）；可访问 URL = `/media/{cover_path}`（D24）",
    )
    tags: list[str] = Field(default_factory=list, max_length=8)
    runnable: list[str] | None = Field(default=None, description="可站内运行的语言（§20.1）")
    lang: str = Field(default="zh", min_length=2, max_length=10)
    source: ResourceSource = ResourceSource.REPO

    @field_validator("runnable", mode="before")
    @classmethod
    def _coerce_runnable(cls, value):
        return _split_runnable(value)


class TocNode(_Model):
    """正文目录节点（§11 详情页「目录树」，M2 实现）。"""

    level: int = Field(ge=2, le=6)
    text: str = Field(min_length=1, max_length=200)
    anchor: str = Field(min_length=1, max_length=200)


class ResourceRef(_Model):
    """上一节 / 下一节的轻量引用。"""

    slug: Slug
    title: str = Field(min_length=1, max_length=80)


class ResourceDetail(ResourceSummary):
    """GET /api/resources/{slug}。正文入库而非运行时读文件（§17.2-4）。"""

    body_md: str = Field(default="", max_length=2_000_000)
    file_path: str = Field(default="", max_length=300)
    content_hash: str = Field(default="", max_length=64)
    is_deleted: int = Field(default=0, ge=0, le=1)
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    toc: list[TocNode] = Field(default_factory=list)
    prev: ResourceRef | None = None
    next: ResourceRef | None = None
    media_base: str = Field(
        default="",
        max_length=300,
        description=(
            "正文媒体的基目录（content 根相对路径，如 `frontend/react`）；"
            "相对 `src` 解析为 `/media/{media_base}/{src}`，空串表示按 content 根解析（D24）"
        ),
    )


class ResourceListResponse(_Model):
    """分页信封（§11 约定：{ items, total, page, size, took_ms }）。"""

    items: list[ResourceSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)
    took_ms: int | None = Field(default=None, ge=0)
    index_version: str | None = Field(default=None, max_length=64)


class RelatedResponse(_Model):
    """GET /api/resources/{slug}/related：同分类 + 语义近邻，最多 6 条（§11）。"""

    items: list[ResourceSummary] = Field(default_factory=list, max_length=6)
    total: int = Field(ge=0)
    semantic_available: bool = False


# ----------------------------------------------------------------- 检索
class SearchHit(_Model):
    """搜索结果条目。snippet 里命中词用方括号包裹（**不是 HTML**，§13.2-2）。"""

    slug: Slug
    title: str = Field(min_length=1, max_length=80)
    snippet: str = Field(default="", max_length=400)
    stack: TokenStr
    category: TokenStr
    level: Level
    type: ResourceType
    duration_min: int | None = Field(default=None, ge=0, le=600)
    author: str = Field(default="", max_length=120)
    license: str = Field(default="", max_length=64)
    score: float = Field(ge=0.0, le=1.0)


class SearchResponse(_Model):
    """GET /api/search 与 POST /api/search 的**同一个**响应结构（DECISIONS D1）。"""

    query: str = Field(min_length=1, max_length=100)
    mode: SearchMode
    tokens: list[str] = Field(default_factory=list, max_length=20)
    total: int = Field(ge=0)
    items: list[SearchHit] = Field(default_factory=list)
    took_ms: int | None = Field(default=None, ge=0)
    semantic_available: bool | None = Field(
        default=None,
        description="None=本次没走语义检索；False=已配置但不可用（§7.9 降级提示）",
    )
    notice: str | None = Field(default=None, max_length=200)


class SearchRequest(_Request):
    """POST /api/search 的 JSON body（命令面板与 GET 同语义，D1）。"""

    q: str = Field(min_length=1, max_length=100)
    stack: TokenStr | None = None
    category: TokenStr | None = None
    level: Level | None = None
    type: ResourceType | None = None
    limit: int = Field(default=10, ge=1, le=50)
    include_overview: bool = False


# ------------------------------------------------------------- 计划草稿
class ItemDraft(_Request):
    """plan_draft.stages[].items[]（§7.6）。模板 YAML 与 AI 产出都走这里，
    所以用 forbid：模板里拼错一个键要比对到字段名，不能静默忽略。"""

    title: str = Field(min_length=1, max_length=80)
    resource_slug: Slug | None = None
    est_minutes: int = Field(default=30, ge=10, le=240)
    note: str = Field(default="", max_length=200)


class StageDraft(_Request):
    title: str = Field(min_length=1, max_length=30)
    weeks: int = Field(ge=1, le=52)
    description: str = Field(default="", max_length=200)
    items: list[ItemDraft] = Field(default_factory=list, min_length=1, max_length=20)


class PlanDraft(_Request):
    """唯一事实来源：模板 YAML、AI 产出、落库入参共用这一个结构（§7.6）。

    这里放**结构规则**：单字段约束、阶段数 2–8、各阶段周数之和与 weeks 偏差 ≤20%。
    放不进来的（slug 白名单、总条目 5–40、难度倒挂、总时长偏差 ≤30%）在
    `services/plan_validate.py`，见 MODULES.md 的签名表。
    """

    title: str = Field(min_length=1, max_length=60)
    goal: str = Field(min_length=1, max_length=120)
    weeks: int = Field(ge=4, le=52)
    weekly_hours: int = Field(ge=1, le=40)
    stages: list[StageDraft] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def _weeks_within_tolerance(self):
        """§7.6：各阶段周数之和与 weeks 偏差 ≤20%，超出就是编排不自洽。"""
        total = sum(s.weeks for s in self.stages)
        low, high = self.weeks * 0.8, self.weeks * 1.2
        if total < low or total > high:
            raise ValueError(
                "各阶段周数之和 %d 与计划周期 %d 周偏差超过 20%%" % (total, self.weeks)
            )
        return self


class PlanSlots(_Model):
    """L2 抽取出的槽位（§7.5）。字段全可空——抽不到就不填。"""

    goal: str | None = Field(default=None, max_length=120)
    weeks: int | None = Field(default=None, ge=4, le=52)
    weekly_hours: int | None = Field(default=None, ge=1, le=40)
    level: Level | None = None
    stack: TokenStr | None = None


# ----------------------------------------------------------------- 计划
class StageProgress(_Model):
    """阶段进度 = done / (done + todo)，skipped 不计入分母（§8.4）。"""

    stage_id: int = Field(ge=1)
    done: int = Field(ge=0)
    todo: int = Field(ge=0)
    skipped: int = Field(ge=0)
    percent: float = Field(ge=0.0, le=100.0)
    est_minutes: int = Field(ge=0)


class PlanProgress(_Model):
    """计划总进度**按时长加权**（§8.4），不是条目数平均。"""

    percent: float = Field(ge=0.0, le=100.0)
    done_items: int = Field(ge=0)
    todo_items: int = Field(ge=0)
    skipped_items: int = Field(ge=0)
    total_minutes: int = Field(ge=0)
    invested_minutes: int = Field(ge=0)
    remaining_weeks: float = Field(ge=0.0)
    stages: list[StageProgress] = Field(default_factory=list)


class ItemRead(_Model):
    """plan_items 一行 + 两个实时解析字段（§8.4 标题快照 + 实时覆盖）。"""

    id: int = Field(ge=1)
    stage_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=80, description="保存时的标题快照")
    resource_slug: Slug | None = None
    note: str = Field(default="", max_length=200)
    est_minutes: int = Field(default=30, ge=10, le=240)
    order_index: int = Field(default=0, ge=0)
    status: PlanItemStatus = PlanItemStatus.TODO
    depends_on: list[int] | None = Field(default=None, description="二期节点图预留（§8.5）")
    completed_at: Timestamp | None = None
    version: int = Field(default=1, ge=1)
    is_deleted: int = Field(
        default=0, ge=0, le=1,
        description="软删除标记（schema v2）；正常查询恒为 0，已删除的条目不出现在响应里",
    )
    resolved_title: str | None = Field(
        default=None, max_length=80, description="按 slug 实时查到的标题；null=资料已下架"
    )
    resource_available: bool = True
    resource_type: ResourceType | None = Field(
        default=None,
        description=(
            "按 resource_slug 从 resources 表实时解析的资料类型（§8.5 条目行类型图标/"
            "「只看视频」筛选，D25）；null = 未绑定资料或资料已下架"
        ),
    )
    resource_level: Level | None = Field(
        default=None,
        description=(
            "按 resource_slug 从 resources 表实时解析的难度（§8.5 难度徽章，D25）；"
            "null 语义同 resource_type"
        ),
    )


class StageRead(_Model):
    id: int = Field(ge=1)
    plan_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=200)
    weeks: int = Field(default=1, ge=1, le=52)
    order_index: int = Field(default=0, ge=0)
    items: list[ItemRead] = Field(default_factory=list)
    progress: StageProgress | None = None


class PlanRead(_Model):
    """计划详情：三层结构 + 进度（§8.1 / §11 GET /api/plans/{id}）。

    `DELETE /api/plans/{id}` 的响应就是本模型，`is_deleted=1`（§11「软删除」）。
    """

    id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=60)
    goal: str = Field(default="", max_length=120)
    start_level: Level = Level.BEGINNER
    weekly_hours: int = Field(default=10, ge=1, le=40)
    horizon_weeks: int = Field(default=12, ge=4, le=52)
    status: PlanStatus = PlanStatus.ACTIVE
    origin: PlanOrigin = PlanOrigin.MANUAL
    version: int = Field(default=1, ge=1)
    created_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    is_deleted: int = Field(
        default=0, ge=0, le=1,
        description="软删除标记（schema v2）：0=正常，1=已删除；GET 只返回 0",
    )
    stages: list[StageRead] = Field(default_factory=list)
    progress: PlanProgress | None = None


class PlanSummary(_Model):
    """计划列表一行：不带 stages，只有进度摘要（§11）。"""

    id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=60)
    goal: str = Field(default="", max_length=120)
    start_level: Level = Level.BEGINNER
    weekly_hours: int = Field(default=10, ge=1, le=40)
    horizon_weeks: int = Field(default=12, ge=4, le=52)
    status: PlanStatus = PlanStatus.ACTIVE
    origin: PlanOrigin = PlanOrigin.MANUAL
    version: int = Field(default=1, ge=1)
    updated_at: Timestamp | None = None
    is_deleted: int = Field(default=0, ge=0, le=1, description="软删除标记；列表恒为 0")
    progress: PlanProgress | None = None


class PlanListResponse(_Model):
    items: list[PlanSummary] = Field(default_factory=list)
    total: int = Field(ge=0)
    active_plan_id: int | None = Field(default=None, ge=1, description="侧栏要显示的那个计划")


class PlanCreate(_Request):
    """POST /api/plans。草稿与手动创建共用；origin 表明是哪条路径来的（§8.6）。

    创建类请求**不带 version**（还没有实体可以冲突）。
    """

    draft: PlanDraft
    origin: PlanOrigin = PlanOrigin.MANUAL
    template_id: str | None = Field(default=None, min_length=1, max_length=64)
    start_level: Level = Level.BEGINNER
    set_active: bool = True


class PlanPatch(_Request):
    """PATCH /api/plans/{id}：改名 / 归档（§8.3）。version = plans.version。"""

    title: str | None = Field(default=None, min_length=1, max_length=60)
    goal: str | None = Field(default=None, min_length=1, max_length=120)
    status: PlanStatus | None = None
    set_active: bool | None = Field(default=None, description="true 时把其它 active 计划归档")
    version: int = Field(ge=1)


class StageCreate(_Request):
    """POST /api/plans/{id}/stages。并发令牌是**所属计划**的 version（D4）。"""

    title: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=200)
    weeks: int = Field(default=1, ge=1, le=52)
    order_index: int | None = Field(default=None, ge=0)
    version: int = Field(ge=1)


class StagePatch(_Request):
    """PATCH /api/stages/{id}。version 同样是 plans.version（plan_stages 无 version 列）。"""

    title: str | None = Field(default=None, min_length=1, max_length=30)
    description: str | None = Field(default=None, max_length=200)
    weeks: int | None = Field(default=None, ge=1, le=52)
    order_index: int | None = Field(default=None, ge=0)
    version: int = Field(ge=1)


class ItemCreate(_Request):
    """POST /api/plan-items。可绑资料也可纯文字（§17.2-5）。version = plans.version。"""

    stage_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=80)
    resource_slug: Slug | None = None
    note: str = Field(default="", max_length=200)
    est_minutes: int = Field(default=30, ge=10, le=240)
    order_index: int | None = Field(default=None, ge=0)
    version: int = Field(ge=1)


class ItemPatch(_Request):
    """PATCH /api/plan-items/{id}：状态 / 时长 / 备注 / 跨阶段 / 排序（§8.3）。

    `resource_slug` 传 null 表示解绑（前端要把「未提供」与「显式置空」区分开，
    实现侧用 model_fields_set 判断）。
    """

    title: str | None = Field(default=None, min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=200)
    est_minutes: int | None = Field(default=None, ge=10, le=240)
    status: PlanItemStatus | None = None
    stage_id: int | None = Field(default=None, ge=1)
    resource_slug: Slug | None = None
    order_index: int | None = Field(default=None, ge=0)
    version: int = Field(ge=1)


class PlanMutationResult(_Model):
    """计划树子资源（阶段/条目）的增删改统一响应（D6）。

    返回**整棵更新后的计划**，前端一次 `setQueryData` 就对齐，不必补一次详情请求；
    这也是唯一能让前端拿到最新 `plans.version` 的地方。
    """

    plan: PlanRead
    affected_stage_ids: list[int] = Field(default_factory=list)
    affected_item_ids: list[int] = Field(default_factory=list)


class PlanTemplateSummary(_Model):
    """模板列表一行（模板是 content/_meta/templates/*.yml，§8.6）。"""

    template_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=60)
    goal: str = Field(min_length=1, max_length=120)
    weeks: int = Field(ge=4, le=52)
    weekly_hours: int = Field(ge=1, le=40)
    stage_count: int = Field(ge=2, le=8)
    item_count: int = Field(ge=0)
    label: LocalizedLabel | None = None


class PlanTemplateListResponse(_Model):
    items: list[PlanTemplateSummary] = Field(default_factory=list)
    total: int = Field(ge=0)


# ----------------------------------------------------------------- 助手
class PageContext(_Model):
    """用户当前正在看的页面（§7.8，作为上下文而非历史）。"""

    resource_slug: Slug | None = None
    resource_title: str | None = Field(default=None, max_length=80)
    plan_id: int | None = Field(default=None, ge=1)


class AgentExtractRequest(_Request):
    """POST /api/agent/extract：一句话 → 表单字段（§8.2 第 2 步）。"""

    text: str = Field(min_length=1, max_length=500)
    locale: Locale = "zh-CN"
    page_context: PageContext | None = None


class AgentExtractResponse(_Model):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    slots: PlanSlots = Field(default_factory=PlanSlots)
    needs_clarify: bool = False
    clarify_options: list[str] = Field(default_factory=list, max_length=3)


class AgentPlanDraftRequest(_Request):
    """POST /api/agent/plan-draft：表单 → 草稿（不落库，§7.6）。

    `template_id` 非空时走「套用模板」（0 token）；为空走两段式生成。
    """

    goal: str = Field(min_length=1, max_length=120)
    level: Level = Level.BEGINNER
    weekly_hours: int = Field(default=10, ge=1, le=40)
    weeks: int = Field(default=12, ge=4, le=52)
    prefer: PreferType | None = None
    stack: TokenStr | None = None
    template_id: str | None = Field(default=None, min_length=1, max_length=64)


class AgentChatRequest(_Request):
    """POST /api/agent/chat（SSE）。历史由服务端维护，客户端不传（§7.8）。"""

    message: str = Field(min_length=1, max_length=500)
    locale: Locale = "zh-CN"
    page_context: PageContext | None = None


class TokenUsage(_Model):
    """成本显示用（§7.10）。上游不返回 usage 时为估算值，estimated=true。"""

    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    estimated: bool = True


class SseIntentData(_Model):
    """`event: intent` 的 data。"""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    source: RouteSource = "rule"
    slots: PlanSlots | None = None
    needs_clarify: bool = False
    clarify_options: list[str] = Field(default_factory=list, max_length=3)


class SseDeltaData(_Model):
    """`event: delta` 的 data：增量文本，前端按顺序拼接。"""

    text: str = Field(min_length=1, max_length=4000)


class SseDoneData(_Model):
    """`event: done`：流正常结束（也是唯一携带结构化结果的载体）。"""

    kind: DoneKind = "text"
    message_id: int | None = Field(default=None, ge=1)
    finish_reason: FinishReason = "stop"
    aborted: bool = False
    usage: TokenUsage = Field(default_factory=TokenUsage)
    intent: Intent | None = None
    result: SearchResponse | PlanDraft | None = None


class SseErrorData(ErrorEnvelope):
    """`event: error` 的 data：与错误信封同形，发完立即关闭流（不再发 done）。"""


class SseFrame(_Model):
    """SSE 帧样板：`event: <name>\\ndata: <json>\\n\\n`（LF，单行 data）。

    只做格式声明，不含业务逻辑；生产实现照这个拼字节，测试照这个断言。
    """

    event: SSEEventName
    data: str = Field(min_length=1)

    def to_frame(self) -> str:
        return "event: %s\ndata: %s\n\n" % (self.event, self.data.replace("\n", " "))


# ----------------------------------------------------------------- 设置
class LLMConfigRead(_Model):
    """读接口：api_key 永远只回掩码（§13.1）。"""

    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=100)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=256, le=8192)
    api_key_masked: str | None = Field(default=None, max_length=40)


class EmbeddingConfigRead(_Model):
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=100)
    dim: int | None = Field(default=None, ge=1, le=8192)
    api_key_masked: str | None = Field(default=None, max_length=40)


class LLMConfigWrite(_Request):
    """写接口：api_key 只写不读（回读恒为掩码）。"""

    base_url: str = Field(min_length=1, max_length=300, pattern=HTTP_URL_PATTERN)
    model: str = Field(min_length=1, max_length=100)
    api_key: str | None = Field(default=None, min_length=8, max_length=200)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=256, le=8192)


class EmbeddingConfigWrite(_Request):
    base_url: str = Field(min_length=1, max_length=300, pattern=HTTP_URL_PATTERN)
    model: str = Field(min_length=1, max_length=100)
    api_key: str | None = Field(default=None, min_length=8, max_length=200)
    dim: int | None = Field(default=None, ge=1, le=8192)


class SettingsRead(_Model):
    """GET /api/settings（§10）。密钥只出掩码，secret_backend 说明存的哪一级（§13.8）。"""

    llm: LLMConfigRead = Field(default_factory=LLMConfigRead)
    embedding: EmbeddingConfigRead = Field(default_factory=EmbeddingConfigRead)
    private_dir: str | None = Field(default=None, max_length=500)
    language: Locale = "zh-CN"
    code_runner_enabled: bool = True
    onboarded: bool = False
    secret_backend: SecretBackend = "keyring"
    llm_configured: bool = False
    embedding_configured: bool = False


class SettingsWrite(_Request):
    """PUT /api/settings：分组可选，未提供的组保持原值（局部更新语义）。"""

    llm: LLMConfigWrite | None = None
    embedding: EmbeddingConfigWrite | None = None
    private_dir: str | None = Field(default=None, max_length=500)
    language: Locale | None = None
    code_runner_enabled: bool | None = None


class SettingsTestRequest(_Request):
    """POST /api/settings/test：保存前先探活（§10「保存前发一条 1 token 探针」）。

    带 override 时用请求里的配置测，不落库。
    """

    kind: SettingsTestKind
    llm: LLMConfigWrite | None = None
    embedding: EmbeddingConfigWrite | None = None


class SettingsTestResponse(_Model):
    ok: bool
    kind: SettingsTestKind
    latency_ms: int = Field(ge=0)
    model: str | None = Field(default=None, max_length=100)
    message: str = Field(default="", max_length=300)
    error_code: str | None = Field(default=None, max_length=64)


# ----------------------------------------------------------------- 索引
class IndexStatus(_Model):
    """GET /api/index/status（§6.1 第 4 步：重建期间前端轮询它）。"""

    state: IndexRunState = "idle"
    mode: IndexMode | None = None
    task_id: str | None = Field(default=None, max_length=64)
    files_total: int = Field(default=0, ge=0)
    files_done: int = Field(default=0, ge=0)
    percent: float = Field(default=0.0, ge=0.0, le=100.0)
    started_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    last_indexed_at: Timestamp | None = None
    content_version: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=500)


class IndexRebuildRequest(_Request):
    mode: IndexMode = "full"


class IndexRebuildResponse(_Model):
    task_id: str = Field(min_length=1, max_length=64)
    accepted: bool = True
    status: IndexStatus = Field(default_factory=IndexStatus)


class PrivateRescanRequest(_Request):
    """POST /api/private/rescan：无参数（目录取自设置），body 可省略。"""


class PrivateRescanResponse(_Model):
    root: str = Field(min_length=1, max_length=500)
    scanned: int = Field(default=0, ge=0)
    added: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    removed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list, max_length=50)
    took_ms: int = Field(default=0, ge=0)


__all__ = [
    # 常量
    "ISO8601_UTC", "SLUG_PATTERN", "TOKEN_PATTERN", "HTTP_URL_PATTERN",
    # 枚举 / 字面量
    "Intent", "Level", "ResourceType", "ResourceSource", "PlanStatus", "PlanOrigin",
    "PlanItemStatus", "SearchMode", "Locale", "SSEEventName", "DoneKind",
    "FinishReason", "RouteSource", "SecretBackend", "IndexRunState", "IndexMode",
    "PreferType", "SettingsTestKind", "ResourceSort", "PlanStatusFilter",
    # 错误
    "ErrorEnvelope",
    # 健康 / 分类
    "IndexState", "HealthResponse", "LocalizedLabel", "CategoryNode", "StackNode",
    "TaxonomyResponse",
    # 资料
    "ResourceSummary", "ResourceDetail", "TocNode", "ResourceRef",
    "ResourceListResponse", "RelatedResponse",
    # 检索
    "SearchHit", "SearchResponse", "SearchRequest",
    # 草稿
    "ItemDraft", "StageDraft", "PlanDraft", "PlanSlots",
    # 计划
    "StageProgress", "PlanProgress", "ItemRead", "StageRead", "PlanRead",
    "PlanSummary", "PlanListResponse", "PlanCreate", "PlanPatch", "StageCreate",
    "StagePatch", "ItemCreate", "ItemPatch", "PlanMutationResult", "PlanTemplateSummary",
    "PlanTemplateListResponse",
    # 助手
    "PageContext", "AgentExtractRequest", "AgentExtractResponse",
    "AgentPlanDraftRequest", "AgentChatRequest", "TokenUsage",
    "SseIntentData", "SseDeltaData", "SseDoneData", "SseErrorData", "SseFrame",
    # 设置
    "LLMConfigRead", "EmbeddingConfigRead", "LLMConfigWrite", "EmbeddingConfigWrite",
    "SettingsRead", "SettingsWrite", "SettingsTestRequest", "SettingsTestResponse",
    # 索引 / 私有资料
    "IndexStatus", "IndexRebuildRequest", "IndexRebuildResponse",
    "PrivateRescanRequest", "PrivateRescanResponse",
]
