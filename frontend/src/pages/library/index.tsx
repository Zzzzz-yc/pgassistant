/**
 * 资料库 —— `/library`（Owner：资料页 M2；需求文档 §9.5 / §9.6 / §9.9）。
 *
 * 结构：左侧筛选栏（可折叠，移动端收进 Sheet）+ 右侧网格（3/2/1 列）+ 分页；
 * 顶部「共 N 篇 · 用时 Xms」。**所有筛选与排序都同步到 URL query**（§16 验收项
 * “筛选条件进入 URL，刷新/分享不丢”），返回时用 `useScrollRestore` 恢复滚动位置。
 *
 * 三态齐全：Loading（与真实网格同尺寸的骨架，>3s 追加“仍在加载…”）、
 * Empty（插画 + 行动按钮）、Error（错误码 + 人话 + 重试 + 复制详情）。
 * 零结果（`q` 有值且 total=0）额外给 §9.9 的三个动作。
 */
import { Search as SearchIcon, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import { ResourceCard } from "@/components/business/ResourceCard";
import { typeLabel } from "@/components/business/TypeIcon";
import {
  Badge,
  Button,
  Card,
  Input,
  Pagination,
  Select,
  Sheet,
  Skeleton,
  Tag,
  type SelectOption,
} from "@/components/ui";
import { useDebouncedValue } from "@/lib/query";
import type {
  Level,
  ResourceListQuery,
  ResourceSort,
  ResourceSource,
  ResourceType,
} from "@/types/api";

import { AddToPlanDialog } from "@/features/library/AddToPlanDialog";
import {
  getScrollContainer,
  useActivePlan,
  useResourceList,
  useScrollRestore,
  useTaxonomyData,
} from "@/features/library/hooks";
import { localizedLabel } from "@/lib/labels";
import { mediaUrl, resourceHref } from "@/features/library/media";

import "./library.css";

const PAGE_SIZE = 24;
/** `Select` 用 `undefined` 表示未选，但 Radix 需要字符串值，所以拿它当“全部”。 */
const ALL = "__all__";

const SORT_OPTIONS: ResourceSort[] = ["default", "title", "duration_min", "updated_at"];
const SOURCE_OPTIONS: ResourceSource[] = ["repo", "private"];
const LEVEL_OPTIONS: Level[] = ["beginner", "intermediate", "advanced"];

const FILTER_KEYS = ["stack", "category", "level", "type", "source", "q"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];

function readQuery(params: URLSearchParams): ResourceListQuery {
  const text = params.get("q")?.trim();
  return {
    stack: params.get("stack") || undefined,
    category: params.get("category") || undefined,
    level: (params.get("level") as Level) || undefined,
    type: (params.get("type") as ResourceType) || undefined,
    source: (params.get("source") as ResourceSource) || undefined,
    q: text || undefined,
    sort: (params.get("sort") as ResourceSort) || undefined,
    page: Math.max(1, Number(params.get("page") ?? "1") || 1),
    size: PAGE_SIZE,
  };
}

export default function LibraryPage() {
  const { t, i18n } = useTranslation("library");
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => readQuery(params), [params]);

  useScrollRestore("library");

  const taxonomy = useTaxonomyData();
  const list = useResourceList(query);
  const activePlan = useActivePlan();

  const [sheetOpen, setSheetOpen] = useState(false);
  const [slow, setSlow] = useState(false);
  const [addTarget, setAddTarget] = useState<{ slug: string; title: string } | null>(null);

  // §9.6：超过 3s 追加“仍在加载…”
  useEffect(() => {
    if (!list.isLoading) {
      setSlow(false);
      return;
    }
    const timer = window.setTimeout(() => setSlow(true), 3000);
    return () => window.clearTimeout(timer);
  }, [list.isLoading]);

  /**
   * 一次改多个 query 键（例如“换技术栈时顺手清掉分类”）。
   *
   * 必须合并成一次 `setParams`：分两次调用的话第二次拿到的是**旧的** `params`
   * 闭包，第一次的改动会被覆盖掉。
   */
  const setFilters = useCallback(
    (changes: Partial<Record<FilterKey | "sort" | "page", string | null>>) => {
      const next = new URLSearchParams(params);
      for (const [key, value] of Object.entries(changes)) {
        const trimmed = value?.trim();
        if (trimmed) next.set(key, trimmed);
        else next.delete(key);
      }
      // 改筛选/排序回第 1 页，除非本次明确在翻页（分页组件自己会带 page）
      if (!("page" in changes)) next.delete("page");
      setParams(next, { replace: "page" in changes });
    },
    [params, setParams],
  );

  const setFilter = useCallback(
    (key: FilterKey | "sort" | "page", value: string | null) => setFilters({ [key]: value }),
    [setFilters],
  );

  const clearFilters = useCallback(() => {
    const next = new URLSearchParams();
    // 排序不是筛选条件：清筛选时保留用户选的排序
    const sort = params.get("sort");
    if (sort) next.set("sort", sort);
    setParams(next);
  }, [params, setParams]);

  const activeFilters = useMemo(
    () => FILTER_KEYS.filter((key) => Boolean(params.get(key))),
    [params],
  );

  const data = list.data;
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const items = data?.items ?? [];
  const hasQuery = Boolean(query.q);
  const zeroResult = !list.isLoading && !list.error && hasQuery && total === 0;

  // 显示名按当前界面语言取 label，拿不到回退原始 key（D28；值仍是 key）。
  const language = i18n.resolvedLanguage ?? i18n.language;

  const stackOptions = useMemo<SelectOption[]>(() => {
    const all: SelectOption[] = [{ value: ALL, label: t("filter.all") }];
    for (const node of taxonomy.data?.stacks ?? []) {
      all.push({
        value: node.stack,
        label: `${localizedLabel(node.label, language) ?? node.stack} · ${node.total}`,
      });
    }
    return all;
  }, [taxonomy.data, t, language]);

  const categoryOptions = useMemo<SelectOption[]>(() => {
    const all: SelectOption[] = [{ value: ALL, label: t("filter.all") }];
    const stacks = taxonomy.data?.stacks ?? [];
    const scoped = query.stack ? stacks.filter((node) => node.stack === query.stack) : stacks;
    const seen = new Set<string>();
    for (const node of scoped) {
      const stackName = localizedLabel(node.label, language) ?? node.stack;
      for (const category of node.categories) {
        if (seen.has(category.category)) continue;
        seen.add(category.category);
        const categoryName = localizedLabel(category.label, language) ?? category.category;
        all.push({
          value: category.category,
          label: query.stack ? categoryName : `${stackName} / ${categoryName}`,
        });
      }
    }
    return all;
  }, [taxonomy.data, query.stack, t, language]);

  const typeOptions = useMemo<SelectOption[]>(() => {
    // 选项来源是接口返回值，不写死 8 值枚举（D19）
    const all: SelectOption[] = [{ value: ALL, label: t("filter.all") }];
    for (const type of taxonomy.data?.types ?? []) {
      all.push({ value: type, label: typeLabel(type) });
    }
    return all;
  }, [taxonomy.data, t]);

  const levelOptions = useMemo<SelectOption[]>(
    () => [
      { value: ALL, label: t("filter.all") },
      ...LEVEL_OPTIONS.map((level) => ({ value: level, label: t(`level.${level}`) })),
    ],
    [t],
  );

  const sourceOptions = useMemo<SelectOption[]>(
    () => [
      { value: ALL, label: t("filter.all") },
      ...SOURCE_OPTIONS.map((source) => ({ value: source, label: t(`source.${source}`) })),
    ],
    [t],
  );

  const filterControls = (
    <div className="lib-filters">
      <SearchField
        value={params.get("q") ?? ""}
        onCommit={(value) => setFilter("q", value)}
        label={t("filter.q")}
        placeholder={t("filter.qPlaceholder")}
      />

      <Select
        label={t("filter.stack")}
        ariaLabel={t("filter.stack")}
        value={query.stack ?? ALL}
        onValueChange={(value) => {
          // 换技术栈后原来选中的分类可能不属于它，一并清掉（一次提交，避免覆盖）
          setFilters({ stack: value === ALL ? null : value, category: null });
        }}
        options={stackOptions}
        loading={taxonomy.isLoading}
      />

      <Select
        label={t("filter.category")}
        ariaLabel={t("filter.category")}
        value={query.category ?? ALL}
        onValueChange={(value) => setFilter("category", value === ALL ? null : value)}
        options={categoryOptions}
        loading={taxonomy.isLoading}
      />

      <Select
        label={t("filter.level")}
        ariaLabel={t("filter.level")}
        value={query.level ?? ALL}
        onValueChange={(value) => setFilter("level", value === ALL ? null : value)}
        options={levelOptions}
      />

      <Select
        label={t("filter.type")}
        ariaLabel={t("filter.type")}
        value={query.type ?? ALL}
        onValueChange={(value) => setFilter("type", value === ALL ? null : value)}
        options={typeOptions}
        loading={taxonomy.isLoading}
      />

      <Select
        label={t("filter.source")}
        ariaLabel={t("filter.source")}
        value={query.source ?? ALL}
        onValueChange={(value) => setFilter("source", value === ALL ? null : value)}
        options={sourceOptions}
      />

      {activeFilters.length > 0 ? (
        <Button variant="ghost" size="sm" iconLeft={<X size={13} />} onClick={clearFilters}>
          {t("filter.clear")}
        </Button>
      ) : null}
    </div>
  );

  return (
    <section className="page-enter lib">
      <header className="lib__head">
        <div className="lib__headline">
          <h1 className="lib__title">{t("title")}</h1>
          <p className="lib__count" aria-live="polite">
            {list.isLoading
              ? t("countLoading")
              : list.error
                ? t("countFailed")
                : t("count", { total, ms: data?.took_ms ?? 0 })}
            {slow ? <span className="lib__slow"> {t("stillLoading")}</span> : null}
          </p>
        </div>

        <div className="lib__actions">
          <div className="lib__sort">
            <Select
              ariaLabel={t("sort.label")}
              value={query.sort ?? "default"}
              onValueChange={(value) => setFilter("sort", value === "default" ? null : value)}
              options={SORT_OPTIONS.map((sort) => ({ value: sort, label: t(`sort.${sort}`) }))}
            />
          </div>
          <Button
            variant="secondary"
            className="lib__filter-toggle"
            iconLeft={<SlidersHorizontal size={14} />}
            onClick={() => setSheetOpen(true)}
          >
            {t("filter.open")}
            {activeFilters.length > 0 ? (
              <Badge tone="accent" size="sm">
                {activeFilters.length}
              </Badge>
            ) : null}
          </Button>
        </div>
      </header>

      <div className="lib__body">
        <aside className="lib__aside" aria-label={t("filter.region")}>
          {filterControls}
        </aside>

        <div className="lib__main">
          {activeFilters.length > 0 ? (
            <ul className="lib__chips">
              {activeFilters.map((key) => (
                <li key={key}>
                  <Tag
                    selected
                    size="sm"
                    onRemove={() => setFilter(key, null)}
                    removeLabel={t("filter.remove", { name: t(`filter.${key}`) })}
                  >
                    {t(`filter.${key}`)}: {params.get(key)}
                  </Tag>
                </li>
              ))}
            </ul>
          ) : null}

          {list.error ? (
            <ErrorState
              code={list.error.code}
              message={list.error.message}
              hint={list.error.hint}
              detail={list.error.detail}
              requestId={list.error.requestId ?? null}
              onRetry={() => void list.refetch()}
              retrying={list.isFetching}
            />
          ) : list.isLoading ? (
            <ul className="lib__grid" aria-busy="true">
              {[0, 1, 2, 3, 4, 5].map((index) => (
                <li key={index}>
                  <Card padded={false} className="lib__skeleton">
                    <Skeleton height={132} />
                    <div className="lib__skeleton-body">
                      <Skeleton height={18} width="70%" />
                      <Skeleton height={14} />
                      <Skeleton height={14} width="50%" />
                    </div>
                  </Card>
                </li>
              ))}
            </ul>
          ) : zeroResult ? (
            <ZeroResult
              query={query.q ?? ""}
              onClearFilters={clearFilters}
              onSemantic={() => navigate("/settings")}
              onAskAssistant={() => navigate("/assistant")}
            />
          ) : items.length === 0 ? (
            <EmptyState
              illustration="library"
              title={t("empty.title")}
              description={t("empty.description")}
              action={
                activeFilters.length > 0 ? (
                  <Button size="sm" onClick={clearFilters}>
                    {t("empty.action")}
                  </Button>
                ) : (
                  <Button size="sm" onClick={() => navigate("/plans/new")}>
                    {t("empty.planAction")}
                  </Button>
                )
              }
            />
          ) : (
            <ul className="lib__grid">
              {items.map((item) => (
                <li key={item.slug}>
                  <ResourceCard
                    title={item.title}
                    summary={item.summary}
                    href={resourceHref(item.slug)}
                    onOpen={() => navigate(resourceHref(item.slug))}
                    coverUrl={mediaUrl(item.cover_path)}
                    tags={item.tags}
                    durationMin={item.duration_min}
                    level={item.level}
                    type={item.type}
                    source={item.source}
                    onAddToPlan={
                      activePlan.data?.active_plan_id
                        ? () => setAddTarget({ slug: item.slug, title: item.title })
                        : undefined
                    }
                  />
                </li>
              ))}
            </ul>
          )}

          {totalPages > 1 && !list.error ? (
            <div className="lib__pager">
              <Pagination
                page={query.page ?? 1}
                totalPages={totalPages}
                loading={list.isFetching}
                onChange={(page) => {
                  setFilter("page", String(page));
                  const container = getScrollContainer();
                  if (container === document.scrollingElement) {
                    window.scrollTo({ top: 0, behavior: "smooth" });
                  } else {
                    container?.scrollTo({ top: 0, behavior: "smooth" });
                  }
                }}
                labels={{
                  previous: t("pager.previous"),
                  next: t("pager.next"),
                  navigation: t("pager.navigation"),
                }}
              />
            </div>
          ) : null}
        </div>
      </div>

      <Sheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        side="left"
        title={t("filter.open")}
        ariaLabel={t("filter.region")}
        closeLabel={t("filter.close")}
        footer={
          <Button variant="ghost" onClick={clearFilters} disabled={activeFilters.length === 0}>
            {t("filter.clear")}
          </Button>
        }
      >
        {filterControls}
      </Sheet>

      {addTarget ? (
        <AddToPlanDialog
          open={Boolean(addTarget)}
          onOpenChange={(open) => {
            if (!open) setAddTarget(null);
          }}
          slug={addTarget.slug}
          title={addTarget.title}
        />
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ 子组件 */

/**
 * 受控搜索框：输入期间只改本地状态，防抖 350ms 才写 URL。
 * 否则每敲一个字都会进历史栈、触发一次请求（`q` 是服务端筛选，不是联想）。
 */
function SearchField({
  value,
  onCommit,
  label,
  placeholder,
}: {
  value: string;
  onCommit: (value: string) => void;
  label: string;
  placeholder: string;
}) {
  const [draft, setDraft] = useState(value);
  const debounced = useDebouncedValue(draft, 350);

  // URL 变化（返回、清筛选、命令面板跳转）时把输入框同步过来
  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (debounced === value) return;
    onCommit(debounced);
  }, [debounced, value, onCommit]);

  return (
    <Input
      label={label}
      placeholder={placeholder}
      aria-label={label}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      iconLeft={<SearchIcon size={14} />}
      type="search"
    />
  );
}

/** §9.9 搜索零结果：三个动作（去筛选 / 语义搜索未配则引导设置 / 问助手）。 */
function ZeroResult({
  query,
  onClearFilters,
  onSemantic,
  onAskAssistant,
}: {
  query: string;
  onClearFilters: () => void;
  onSemantic: () => void;
  onAskAssistant: () => void;
}) {
  const { t } = useTranslation("library");
  return (
    <EmptyState
      illustration="search"
      title={t("zero.title", { query })}
      description={t("zero.description")}
      action={
        <div className="lib__zero-actions">
          <Button size="sm" onClick={onClearFilters}>
            {t("zero.clear")}
          </Button>
          <Button size="sm" variant="secondary" onClick={onSemantic}>
            {t("zero.semantic")}
          </Button>
          <Button size="sm" variant="ghost" onClick={onAskAssistant}>
            {t("zero.assistant")}
          </Button>
        </div>
      }
    />
  );
}
