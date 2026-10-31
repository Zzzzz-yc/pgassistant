/**
 * 资料详情 —— `/library/:slug(*)`（Owner：资料页 M2；需求文档 §9.5 / §9.6）。
 *
 * 三栏结构：正文 68ch（`MarkdownView` 的默认 `--prose-width`）+ 右侧粘性目录
 * （锚点与正文 id 由同一份 `toc` 驱动，见 `MarkdownView` 的 `rehypeHeadingAnchors`）
 * + 底部的上一节 / 下一节与相关推荐（同分类，最多 3 条展示）。
 *
 * 两个容易踩的点：
 * - `media_base` 由接口给，**不从 slug 推导**（D24）：`MarkdownView` 用它把相对
 *   `src` 拼成 `/media/{media_base}/{src}`，页面不预改写正文；
 * - 资料不存在或已下架（404 `RESOURCE_NOT_FOUND`）走“已下架”空状态，不是白屏、不弹 alert。
 */
import { ArrowLeft, ArrowRight, List, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import { MarkdownView, type CodeActionsRenderer } from "@/components/business/MarkdownView";
import { ResourceCard } from "@/components/business/ResourceCard";
import { DurationLabel } from "@/components/business/DurationLabel";
import { TypeIcon } from "@/components/business/TypeIcon";
import { Badge, Button, Separator, Skeleton, Tag } from "@/components/ui";
import type { ApiError } from "@/lib/http";
import type { ResourceDetail, ResourceSummary, TocNode } from "@/types/api";

import { AddToPlanDialog } from "@/features/library/AddToPlanDialog";
import { getScrollContainer, useRelated, useResource, useTaxonomyData } from "@/features/library/hooks";
import { localizedLabel } from "@/features/library/labels";
import { libraryHref, mediaUrl, resourceHref } from "@/features/library/media";
import { CodeRunnerPanel } from "@/features/runner/components/CodeRunnerPanel";
import { RunButton } from "@/features/runner/components/RunButton";
import { runnerLangOf } from "@/features/runner/languages";
import type { RunnerLang } from "@/features/runner/protocol";
import { useRunnerEnabled } from "@/features/runner/settings";

import "./library.css";

const RELATED_SHOWN = 3;

export default function ResourcePage() {
  const { t, i18n } = useTranslation("library");
  const navigate = useNavigate();
  const params = useParams();
  // 路由是 `library/:slug(*)`（MODULES §3.1）。React Router 6 的 splat 参数既可能叫
  // `slug`（命名 splat，v5 风格），也可能是 `"*"`（v6 的 `library/*`），两种都取，
  // 这样 router.tsx 由前端骨架改成 v6 写法时本页不用改。
  const slug = params.slug ?? params["*"] ?? "";

  const detail = useResource(slug);
  const related = useRelated(slug);
  const taxonomy = useTaxonomyData();
  const [addOpen, setAddOpen] = useState(false);
  // 运行抽屉的请求（M6）：非 null 即展开。只在 `runnable` 资料上可能被点开。
  const [runnerRequest, setRunnerRequest] = useState<{ lang: RunnerLang; code: string } | null>(null);
  const closeRunner = useMemo(() => () => setRunnerRequest(null), []);
  // 设置项 `runner_enabled` 关闭时入口全部消失（§20.5-7）：抽屉也不该留在屏幕上
  const { enabled: runnerEnabled } = useRunnerEnabled();
  const runnerOpen = runnerRequest !== null && runnerEnabled;

  const notFound = detail.error?.code === "RESOURCE_NOT_FOUND";

  if (detail.isLoading) {
    return <DetailSkeleton />;
  }

  if (notFound) {
    return (
      <section className="page-enter rp">
        <EmptyState
          illustration="library"
          title={t("gone.title")}
          description={t("gone.description")}
          action={
            <Button size="sm" iconLeft={<ArrowLeft size={14} />} onClick={() => navigate("/library")}>
              {t("gone.action")}
            </Button>
          }
        />
      </section>
    );
  }

  if (detail.error || !detail.data) {
    const failure = detail.error as ApiError | null;
    return (
      <section className="page-enter rp">
        <ErrorState
          code={failure?.code}
          message={failure?.message}
          hint={failure?.hint}
          detail={failure?.detail}
          requestId={failure?.requestId ?? null}
          onRetry={() => void detail.refetch()}
          retrying={detail.isFetching}
        />
      </section>
    );
  }

  const resource = detail.data;
  const toc = resource.toc ?? [];
  const relatedItems = (related.data?.items ?? []).slice(0, RELATED_SHOWN);
  // 面包屑的显示名来自 taxonomy 的 label（`content/_meta/taxonomy.yml`）；按当前界面语言
  // 取 `zh`/`en`，拿不到就退回原始 stack / category 值（D28：不硬编码语言、不拼 slug）。
  const language = i18n.resolvedLanguage ?? i18n.language;
  const stackNode = taxonomy.data?.stacks.find((node) => node.stack === resource.stack);
  const stackLabel = localizedLabel(stackNode?.label, language) ?? resource.stack;
  const categoryLabel =
    localizedLabel(
      stackNode?.categories.find((item) => item.category === resource.category)?.label,
      language,
    ) ?? resource.category;

  return (
    <section className="page-enter rp">
      <nav className="rp__crumb" aria-label={t("crumb.label")}>
        <Link to="/library">{t("crumb.library")}</Link>
        <span aria-hidden="true">/</span>
        <Link to={libraryHref({ stack: resource.stack })}>
          {stackLabel}
        </Link>
        <span aria-hidden="true">/</span>
        <Link to={libraryHref({ stack: resource.stack, category: resource.category })}>
          {categoryLabel}
        </Link>
      </nav>

      <header className="rp__head">
        <h1 className="rp__title">{resource.title}</h1>

        <div className="rp__meta">
          <span className="rp__meta-item">
            <TypeIcon type={resource.type} showLabel />
          </span>
          <DurationLabel minutes={resource.duration_min} />
          <Badge tone="accent" title={t("level.label")}>
            {t(`level.${resource.level}`)}
          </Badge>
          {resource.source === "private" ? <Badge tone="accent">{t("source.private")}</Badge> : null}
          {resource.author ? (
            <span className="rp__meta-item rp__muted">{resource.author}</span>
          ) : null}
          {resource.license ? (
            <span className="rp__meta-item rp__muted">{resource.license}</span>
          ) : null}

          <span className="rp__meta-end">
            <Button size="sm" iconLeft={<Plus size={14} />} onClick={() => setAddOpen(true)}>
              {t("addToPlan.cta")}
            </Button>
          </span>
        </div>

        {resource.tags && resource.tags.length > 0 ? (
          <ul className="rp__tags">
            {resource.tags.map((tag) => (
              <li key={tag}>
                <Tag size="sm">{tag}</Tag>
              </li>
            ))}
          </ul>
        ) : null}

        {resource.summary ? <p className="rp__summary">{resource.summary}</p> : null}
      </header>

      <Separator />

      <div className="rp__body">
        <article className="rp__article">
          <ArticleBody
            resource={resource}
            lightboxLabel={t("lightbox")}
            onRun={(lang, code) => setRunnerRequest({ lang, code })}
          />
        </article>

        {/* §9.5：抽屉展开时右侧目录自动隐藏（共用右侧空间），收起即恢复 */}
        {toc.length > 0 && !runnerOpen ? <TocAside nodes={toc} title={t("toc")} /> : null}
      </div>

      <Separator />

      <footer className="rp__foot">
        <nav className="rp__siblings" aria-label={t("siblings")}>
          {resource.prev ? (
            <Link className="rp__sibling" to={resourceHref(resource.prev.slug)}>
              <span className="rp__sibling-dir">
                <ArrowLeft size={13} /> {t("prev")}
              </span>
              <span className="rp__sibling-title">{resource.prev.title}</span>
            </Link>
          ) : (
            <span className="rp__sibling rp__sibling--disabled">{t("noPrev")}</span>
          )}
          {resource.next ? (
            <Link className="rp__sibling rp__sibling--next" to={resourceHref(resource.next.slug)}>
              <span className="rp__sibling-dir">
                {t("next")} <ArrowRight size={13} />
              </span>
              <span className="rp__sibling-title">{resource.next.title}</span>
            </Link>
          ) : (
            <span className="rp__sibling rp__sibling--next rp__sibling--disabled">
              {t("noNext")}
            </span>
          )}
        </nav>

        <section className="rp__related">
          <h2 className="rp__section-title">{t("related.title")}</h2>
          {related.isLoading ? (
            <ul className="rp__related-grid" aria-busy="true">
              {[0, 1, 2].map((index) => (
                <li key={index}>
                  <Skeleton height={140} />
                </li>
              ))}
            </ul>
          ) : relatedItems.length === 0 ? (
            <p className="rp__muted">{t("related.empty")}</p>
          ) : (
            <ul className="rp__related-grid">
              {relatedItems.map((item: ResourceSummary) => (
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
                    maxTags={2}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      </footer>

      <AddToPlanDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        slug={resource.slug}
        title={resource.title}
      />

      {/* 运行抽屉（M6）：只有资料标记了 runnable 才可能被打开；关闭即清空，不记历史 */}
      <CodeRunnerPanel
        open={runnerOpen}
        onOpenChange={(open) => {
          if (!open) closeRunner();
        }}
        lang={runnerRequest?.lang ?? "python"}
        initialCode={runnerRequest?.code ?? ""}
        resourceTitle={resource.title}
      />
    </section>
  );
}

/* ------------------------------------------------------------------ 子组件 */

/**
 * 正文 + 代码块运行入口。
 *
 * `codeActions` 用 `useMemo` 固定身份：`MarkdownView` 把它放进内部 `components` 的依赖里，
 * 每次渲染换一个新函数会让整棵 Markdown 子树重新挂载（滚动位置丢失）。所以这里只在
 * `runnable` 变化时重建，且 `runnable` 为空时**一律不传**——非算法页零差异（§20.1）。
 */
function ArticleBody({
  resource,
  lightboxLabel,
  onRun,
}: {
  resource: ResourceDetail;
  lightboxLabel: string;
  onRun: (lang: RunnerLang, code: string) => void;
}) {
  const runnable = resource.runnable;
  const { enabled, supported } = useRunnerEnabled();

  const codeActions = useMemo<CodeActionsRenderer | undefined>(() => {
    if (!runnable || runnable.length === 0) return undefined;
    // 开关关闭 / 浏览器不支持时**连槽位都不挂**，不给 19 个空 span 留位置
    if (!enabled || !supported) return undefined;
    return (lang, code) => (
      <RunButton
        lang={lang}
        code={code}
        runnable={runnable}
        onOpen={({ lang: opened }) => {
          const mapped = runnerLangOf(opened);
          if (mapped) onRun(mapped, code);
        }}
      />
    );
  }, [runnable, onRun, enabled, supported]);

  return (
    <MarkdownView
      markdown={resource.body_md}
      toc={resource.toc ?? []}
      mediaBase={resource.media_base}
      lightboxLabel={lightboxLabel}
      codeActions={codeActions}
    />
  );
}

function DetailSkeleton() {
  return (
    <section className="page-enter rp" aria-busy="true">
      <Skeleton height={14} width="40%" />
      <Skeleton height={34} width="70%" />
      <Skeleton height={16} width="55%" />
      <Separator />
      <div className="rp__body">
        <div className="rp__article">
          <Skeleton height={16} />
          <Skeleton height={16} width="92%" />
          <Skeleton height={16} width="80%" />
          <Skeleton height={220} />
          <Skeleton height={16} width="88%" />
        </div>
      </div>
    </section>
  );
}

/**
 * 粘性目录。
 *
 * 锚点直接来自 `ResourceDetail.toc[].anchor`，与 `MarkdownView` 注入到 h2/h3 的 id
 * 同源（组件先按标题文本匹配、再按顺序匹配，最后才 `slugify` 兜底），所以这里
 * `getElementById(anchor)` 拿到的就是正文里那个标题。
 *
 * 滚动高亮用 IntersectionObserver（不是 scroll 里算 offsetTop）：容器是 `.app-main`，
 * 用滚动容器当 root 才不会被顶栏遮住。
 */
function TocAside({ nodes, title }: { nodes: TocNode[]; title: string }) {
  const [active, setActive] = useState<string>(nodes[0]?.anchor ?? "");
  const tocKey = nodes.map((node) => node.anchor).join("|");

  useEffect(() => {
    const container = getScrollContainer();
    // 文档滚动时 IntersectionObserver 的 root 必须是 null（视口），不能塞 scrollingElement
    const root = container === document.scrollingElement ? null : container;

    const elements = nodes
      .map((node) => document.getElementById(node.anchor))
      .filter((element): element is HTMLElement => element !== null);
    if (elements.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { root, rootMargin: "-72px 0px -70% 0px", threshold: [0, 1] },
    );
    elements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
    // tocKey 把“节点列表变了”编码进依赖，避免每次渲染重建 observer
  }, [tocKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <aside className="rp__toc" aria-label={title}>
      <p className="rp__toc-title">
        <List size={13} aria-hidden="true" /> {title}
      </p>
      <ul className="rp__toc-list">
        {nodes.map((node) => (
          <li key={node.anchor} className={node.level === 3 ? "rp__toc-item rp__toc-item--sub" : "rp__toc-item"}>
            <a
              href={`#${node.anchor}`}
              className={active === node.anchor ? "rp__toc-link rp__toc-link--active" : "rp__toc-link"}
              aria-current={active === node.anchor ? "location" : undefined}
              onClick={(event) => {
                // 交给浏览器的锚点跳转即可：`scroll-margin-top` 在 CSS 里避开顶栏
                event.preventDefault();
                document.getElementById(node.anchor)?.scrollIntoView({ behavior: "smooth", block: "start" });
                setActive(node.anchor);
              }}
            >
              {node.text}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
