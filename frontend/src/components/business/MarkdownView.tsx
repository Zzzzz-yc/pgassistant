/**
 * MarkdownView（业务组件，需求文档 §9.4「MarkdownView：排版样式 + 代码复制 + 图片灯箱 +
 * 外链图标」，§9.2 第 2 条「不出现裸 Markdown」，§13.2 第 1 条「Markdown 一律 sanitize」）。
 *
 * 安全链路（缺一不可）：
 *   原文 → remark-gfm（表格/任务列表/删除线）
 *        → rehype-highlight（代码高亮，只产出 class）
 *        → rehypeHeadingAnchors（h2/h3 注入 id，与后端 toc 对齐）
 *        → **rehype-sanitize**（白名单：禁 script / iframe / style / on* 属性）
 *   另外：react-markdown 默认不渲染原始 HTML（没有 rehype-raw），`javascript:` 链接
 *   由 react-markdown 的 `defaultUrlTransform` 拦掉。私有资料是用户输入，按不可信处理。
 *
 * 五态：hover / focus-visible / active / disabled / loading **不适用**于容器本身——
 * 它是只读渲染；可点的只有链接、复制按钮（Button 自带五态）与图片（`.md__image-button`
 * 在 markdown.css 里写了自己的 hover/active/focus）；内容加载态由调用方用 `Skeleton` 表达。
 */
import { Check, Copy, ExternalLink } from "lucide-react";
import {
  isValidElement,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components, type Options as ReactMarkdownOptions } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { Button, Dialog } from "@/components/ui";
import type { TocNode } from "@/types/api";

import { copyToClipboard } from "./clipboard";
import "./markdown.css";

/* ----------------------------------------------------------- sanitize 白名单 */

/**
 * 在默认白名单上做三处**加法**：
 * 1. `clobberPrefix` 置空：默认会把 id 前缀成 `user-content-`，那样 toc 的 `#anchor` 就废了；
 *    我们的 id 全是自己生成的 slug（不是用户 HTML 里的 id），没有 clobber 风险。
 * 2. `code` 允许 `hljs` 类、`span` 允许 `hljs-*` 类：rehype-highlight 只靠 class 上色，
 *    默认白名单会把它们剥掉（`id` 本来就在全局白名单里，不用加）。
 * 3. `a` 允许 `target` / `rel`：外链在新标签页打开且 `noopener`。
 */
const SANITIZE_SCHEMA = {
  ...defaultSchema,
  clobberPrefix: "",
  attributes: {
    ...defaultSchema.attributes,
    code: [["className", /^language-./, "hljs"]],
    span: [["className", /^hljs-/]],
    a: [...(defaultSchema.attributes?.a ?? []), "target", "rel"],
  },
} as typeof defaultSchema;

/* ------------------------------------------------------------- 标题锚点注入 */

interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

function textOf(node: HastNode): string {
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(textOf).join("");
}

function normalizeText(text: string): string {
  return text.replace(/\s+/g, "").toLowerCase();
}

/** 无 toc 时的兜底 slug：保留中英文与数字，空格转 `-`。 */
function slugify(text: string): string {
  const slug = text
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^\p{L}\p{N}-]/gu, "")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return slug || "section";
}

/**
 * 给 h2/h3 按**文档顺序**注入 id：
 * 1. 先按标题文本在 toc 里找同一条（文本匹配最稳，允许后端文本有空格差异）；
 * 2. 退到"第 i 个 h2/h3 用 toc[i].anchor"（与后端 toc 顺序对齐）；
 * 3. 都没有（无 toc / toc 条目少于标题）才用 `slugify(text)`。
 * 重复 id 追加 `-2`、`-3`，保证 DOM 里唯一。
 */
function rehypeHeadingAnchors(options: { toc?: readonly TocNode[] }) {
  const toc = options?.toc ?? [];
  return (tree: HastNode) => {
    const used = new Set<string>();
    let headingIndex = 0;

    const visit = (node: HastNode) => {
      if (node.type === "element" && node.tagName && /^h[23]$/.test(node.tagName)) {
        const text = textOf(node).trim();
        const byText = toc.find((entry) => normalizeText(entry.text) === normalizeText(text));
        const byOrder = toc[headingIndex]?.anchor;
        const base = byText?.anchor || byOrder || slugify(text);
        headingIndex += 1;

        let id = base;
        let suffix = 2;
        while (used.has(id)) {
          id = `${base}-${suffix}`;
          suffix += 1;
        }
        used.add(id);
        node.properties = { ...(node.properties ?? {}), id };
      }
      for (const child of node.children ?? []) visit(child);
    };

    visit(tree);
  };
}

/* ------------------------------------------------------------------ 代码块 */

function CodeBlock({ children }: { children: ReactNode }) {
  const preRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);

  const firstChild = Array.isArray(children) ? children[0] : children;
  const rawClass =
    isValidElement<{ className?: string }>(firstChild) && typeof firstChild.props.className === "string"
      ? firstChild.props.className
      : "";
  const language = /language-([\w+#.-]+)/.exec(rawClass)?.[1] ?? "text";

  return (
    <div className="md-code">
      <div className="md-code__head">
        <span className="md-code__lang">{language}</span>
        <span className="md-code__copy">
          <Button
            size="sm"
            variant="ghost"
            iconLeft={copied ? <Check size={13} /> : <Copy size={13} />}
            onClick={() => {
              const text = preRef.current?.textContent ?? "";
              void copyToClipboard(text).then((ok) => {
                if (!ok) return;
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1600);
              });
            }}
          >
            {copied ? "已复制" : "复制"}
          </Button>
        </span>
      </div>
      <pre ref={preRef} className="md-code__pre">
        {children}
      </pre>
    </div>
  );
}

/* --------------------------------------------------------------------- 组件 */

export interface MarkdownViewProps {
  /** 正文 Markdown（`ResourceDetail.body_md` 或助手回复）。 */
  markdown: string;
  /** 后端 toc（可选）：按文档顺序给 h2/h3 分配锚点。 */
  toc?: readonly TocNode[];
  /** 正文最大宽度（ch），默认取 tokens 的 68ch。 */
  proseWidth?: number;
  /** 灯箱标题后缀（默认用 alt）。 */
  lightboxLabel?: string;
  className?: string;
}

export function MarkdownView({
  markdown,
  toc,
  proseWidth,
  lightboxLabel = "图片预览",
  className,
}: MarkdownViewProps) {
  const [zoom, setZoom] = useState<{ src: string; alt: string } | null>(null);

  // 显式标注类型：数组里混着"插件"与"[插件, 选项]"两种形状，靠推断会退化成联合类型
  const rehypePlugins = useMemo<NonNullable<ReactMarkdownOptions["rehypePlugins"]>>(
    () => [
      [rehypeHighlight, { ignoreMissing: true, detect: false }],
      [rehypeHeadingAnchors, { toc }],
      [rehypeSanitize, SANITIZE_SCHEMA],
    ],
    [toc],
  );

  const components = useMemo<Components>(
    () => ({
      pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
      img: ({ src, alt, title }) => {
        const source = typeof src === "string" ? src : "";
        if (!source) return null;
        const text = alt ?? "";
        return (
          <button
            type="button"
            className="md__image-button"
            aria-label={`${lightboxLabel}：${text || "无描述图片"}`}
            onClick={() => setZoom({ src: source, alt: text })}
          >
            <img src={source} alt={text} title={title} loading="lazy" />
          </button>
        );
      },
      a: ({ href, children, title }) => {
        const external = typeof href === "string" && /^https?:\/\//i.test(href);
        return (
          <a
            href={href}
            title={title}
            {...(external ? { target: "_blank", rel: "noreferrer noopener" } : {})}
          >
            {children}
            {external ? (
              <>
                <ExternalLink size={11} className="md__external-icon" aria-hidden="true" />
                <span className="visually-hidden">（在新标签页打开）</span>
              </>
            ) : null}
          </a>
        );
      },
    }),
    [lightboxLabel],
  );

  return (
    <>
      <div className={className ? `md ${className}` : "md"} style={proseWidth ? { maxWidth: `${proseWidth}ch` } : undefined}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={rehypePlugins}
          components={components}
        >
          {markdown}
        </ReactMarkdown>
      </div>

      <Dialog
        open={zoom !== null}
        onOpenChange={(open) => {
          if (!open) setZoom(null);
        }}
        title={zoom?.alt || lightboxLabel}
        width={960}
      >
        {zoom ? <img className="md-lightbox__image" src={zoom.src} alt={zoom.alt} /> : null}
      </Dialog>
    </>
  );
}

/** 供页面侧在需要时复用同一套 sanitize 白名单（例如搜索结果片段按 Markdown 渲染）。 */
export { SANITIZE_SCHEMA };
