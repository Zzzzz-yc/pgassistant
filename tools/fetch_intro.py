#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_intro.py — 抓取教程页面并提取**正文主体**，生成项目格式的 Markdown

目标清单在 tools/sources.json，**新增内容只改 JSON，不改代码**：
  {
    "rules": {"only_summary": false, "max_body_chars": 20000, "fetch_interval_sec": 1.0},
    "batches": {
      "p2": [
        {"slug":"js-vite","title":"Vite 入门","url":"https://...","stack":"frontend",
         "category":"tooling","author":"vitejs","license":"MIT","tags":["前端","构建"]}
      ]
    }
  }

抓取规则（见 tools/README.md）：
  - 提取正文主体，保留 h2/h3 标题层级、代码块、表格、列表；
  - 剔除导航、页脚、侧栏、广告、评论区、「本页译自」类样板；
  - 遵守 robots.txt，同域名串行，每次请求间隔 ≥ 1 秒；
  - 纯标准库（urllib + html.parser），不引第三方 HTML 解析库。

用法:
  python tools/fetch_intro.py --out ./content --batch p2
  python tools/fetch_intro.py --out ./content --batch all --only js-closures,sql-intro
  python tools/fetch_intro.py --list            # 看清单现状
  python tools/fetch_intro.py --report report.json   # 额外写一份抓取结果
"""
import argparse
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path

REGISTRY = Path(__file__).with_name("sources.json")

UA = "Mozilla/5.0 (compatible; pgassistant-content-bot/1.0; +https://github.com/pgassistant)"
TIMEOUT = 30
MIN_INTERVAL = 1.0
MAX_RETRY = 2

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}

# 整棵子树丢弃的标签（脚本、样式、交互控件、装饰图标等）
DROP_TAGS = {
    "script", "style", "template", "svg", "path", "g", "use", "symbol",
    "canvas", "iframe", "noscript", "button", "input", "select", "option",
    "textarea", "form", "nav", "footer", "header", "aside", "menu",
    "dialog", "picture", "video", "audio", "object", "embed", "map",
    # MDN / react.dev 的自定义 Web Component
    "mdn-button", "mdn-survey", "mdn-language-always-redirect-button",
    "mdn-notecard",
}

# class / id 命中即丢弃的样板容器
BOILER_ATTR = re.compile(
    r"(?:^|[\s_-])(?:nav|navbar|navigation|sidebar|toc|table-of-contents|breadcrumb"
    r"|menu|cookie|consent|advert|ads?|adsbygoogle|sponsor|promo|share|social"
    r"|related|recommend|prev-next|prevnext|pagination|pager|skip-link"
    r"|edit-page|edit-this-page|feedback|survey|newsletter|subscribe"
    r"|translation-banner|article-footer|page-footer|site-footer|footer-links"
    r"|comment|comments|respond|disqus|archive-list|language-switch"
    r"|example-header"
    r")(?:$|[\s_-])",
    re.I,
)

# 正文段落级别的样板文字（只在短句上生效，避免误删正文）
BOILER_TEXT = re.compile(
    r"^(?:View in English|Always switch|跳到|Skip to|切换语言|本页译自|本篇译自"
    r"|此页面由社区|原文链接|上一篇|下一篇|上一页|下一页"
    r"|分享|收藏|打印|复制代码|广告|赞助|相关推荐|编程学习推荐|本文档使用"
    r"|Last updated|Edit this page|Was this page helpful|在此页面上|报告问题"
    r"|查看英文版|语言：|Fork|Reset|Show more|Show less|Hide|Copy code)",
    re.I,
)

# 回站内推荐 / 广告类小节，整段丢弃
DROP_SECTION = re.compile(r"^(?:相关推荐|相关文章推荐|推荐阅读|编程学习推荐|赞助商|广告|友情链接|更多教程|扩展阅读)$")

# 标题层级映射
HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# 代码块语言别名
LANG_ALIAS = {
    "javascript": "js", "js": "js", "jsx": "jsx", "tsx": "tsx", "typescript": "ts",
    "ts": "ts", "html": "html", "css": "css", "scss": "scss", "shell": "bash",
    "bash": "bash", "sh": "bash", "console": "bash", "plain": "text",
    "plaintext": "text", "text": "text", "python": "python", "py": "python",
    "java": "java", "sql": "sql", "json": "json", "xml": "xml", "yaml": "yaml",
    "c": "c", "cpp": "cpp", "c++": "cpp", "go": "go", "rust": "rust",
    "php": "php", "ruby": "ruby", "diff": "diff", "markdown": "markdown",
    "md": "markdown", "nginx": "nginx", "dockerfile": "dockerfile",
}


# ---------------------------------------------------------------------------
# DOM：用标准库 html.parser 建一棵容错树
# ---------------------------------------------------------------------------
class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.parent = None

    def classes(self):
        return (self.attrs.get("class", "") + " " + self.attrs.get("id", "")).strip()

    def get(self, key, default=""):
        return self.attrs.get(key, default)

    def __repr__(self):  # pragma: no cover - 调试用
        return "<%s %s>" % (self.tag, self.classes()[:40])


BLOCK_LEVEL = {
    "address", "article", "aside", "blockquote", "details", "div", "dl",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    "dt", "dd",
}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class TreeBuilder(HTMLParser):
    """容错建树：结束标签按名字向上找，找不到就忽略。

    浏览器会自动闭合未闭合的 <p>/<h1>/<li>/<tr>/<td> 等标签；真实页面里这种
    畸形嵌套很常见（例如 runoob 的 <h1> 后面直接跟 <h2>），这里做同样处理，
    否则后半个页面会被吞进前一个未闭合标签里。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def _autoclose(self, tag):
        while len(self.stack) > 1:
            top = self.stack[-1].tag
            if top == "p" and tag in BLOCK_LEVEL:
                self.stack.pop()
                continue
            if top in HEADINGS and (tag in HEADINGS or tag in BLOCK_LEVEL):
                self.stack.pop()
                continue
            if top == "li" and tag == "li":
                self.stack.pop()
                continue
            if top in ("td", "th") and tag in ("td", "th", "tr"):
                self.stack.pop()
                continue
            if top == "tr" and tag in ("tr", "tbody", "tfoot", "thead"):
                self.stack.pop()
                continue
            if top in ("dt", "dd") and tag in ("dt", "dd"):
                self.stack.pop()
                continue
            if top == "option" and tag == "option":
                self.stack.pop()
                continue
            break

    def handle_starttag(self, tag, attrs):
        self._autoclose(tag)
        node = Node(tag, attrs)
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, attrs)
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def parse_html(html):
    builder = TreeBuilder()
    try:
        builder.feed(html)
        builder.close()
    except Exception:  # 残缺页面尽量保留已解析部分
        pass
    return builder.root


def iter_nodes(node):
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Node):
            yield cur
            stack.extend(reversed(cur.children))


def find_first(root, pred):
    for node in iter_nodes(root):
        if pred(node):
            return node
    return None


def text_of(node, skip_tags=("svg", "button", "script", "style", "template")):
    """纯文本（用于标题、表格单元格、摘要）。"""
    out = []

    def walk(n):
        if isinstance(n, str):
            out.append(n)
            return
        if n.tag in skip_tags:
            return
        for c in n.children:
            walk(c)

    walk(node)
    return collapse("".join(out))


def collapse(text):
    return re.sub(r"[ \t\r\f\v\u00a0]+", " ", text.replace("\n", " ")).strip()


def is_blocked(node):
    return bool(BOILER_ATTR.search(node.classes())) if node.classes() else False


# ---------------------------------------------------------------------------
# 站点配置：正文容器
# ---------------------------------------------------------------------------
def _by_id(node, wanted):
    return node.attrs.get("id", "").lower() == wanted


def _class_has(node, token):
    return token in node.classes().lower()


def pick_root(doc, url):
    """按站点结构挑正文容器；返回 (root, site)。"""
    host = urllib.parse.urlparse(url).netloc.lower()

    if "runoob.com" in host:
        node = find_first(doc, lambda n: _by_id(n, "content")) \
            or find_first(doc, lambda n: "article-intro" in n.classes()) \
            or find_first(doc, lambda n: "article-body" in n.classes())
        return node, "runoob"

    if "developer.mozilla.org" in host:
        node = find_first(doc, lambda n: n.tag == "main" and _by_id(n, "content"))
        return node, "mdn"

    if "react.dev" in host:
        node = find_first(doc, lambda n: n.tag == "article")
        return node, "react"

    if "typescriptlang.org" in host:
        main = find_first(doc, lambda n: n.tag == "main")
        article = find_first(main, lambda n: n.tag == "article") if main is not None else None
        if article is None:
            article = find_first(doc, lambda n: n.tag == "article")
        md = find_first(article, lambda n: "markdown" in n.classes()) if article is not None else None
        return (md or article), "typescript"

    # 通用兜底
    node = find_first(doc, lambda n: n.tag == "main") \
        or find_first(doc, lambda n: n.tag == "article") \
        or find_first(doc, lambda n: _by_id(n, "content"))
    return node, "generic"


def meta_descriptions(doc):
    wanted = ("description", "og:description", "twitter:description")
    found = []
    metas = [n for n in iter_nodes(doc) if n.tag == "meta"]
    for key in wanted:
        for node in metas:
            name = node.attrs.get("name", "").lower()
            prop = node.attrs.get("property", "").lower()
            if name == key or prop == key:
                text = collapse(node.attrs.get("content", ""))
                if text and text not in found:
                    found.append(text)
    return found


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, site, url):
        self.site = site
        self.url = url
        self.blocks = []
        self.seen = set()
        self.loose = []
        self.skip_level = 0

    def add(self, block):
        block = block.strip("\n")
        if not block.strip():
            return
        key = re.sub(r"\s+", "", block)[:160]
        if key and key in self.seen:
            return
        self.seen.add(key)
        self.blocks.append(block)


def abs_url(base, href):
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "data:")):
        return ""
    full = urllib.parse.urljoin(base, href)
    return full if full.startswith(("http://", "https://")) else ""


def code_span(text):
    text = collapse(text)
    if not text:
        return ""
    ticks = "`"
    while ticks in text:
        ticks += "`"
    return ticks + text + ticks


INLINE_DROP = {"svg", "button", "script", "style", "template", "img", "picture",
               "input", "select", "textarea", "iframe", "noscript", "path"}


def inline(node, ctx, depth=0):
    """把一段节点渲染成行内 Markdown。"""
    if isinstance(node, str):
        return re.sub(r"\s+", " ", node)
    tag = node.tag
    if tag in INLINE_DROP:
        return ""
    if tag == "br":
        return " "
    if tag == "code":
        return code_span(text_of(node))
    if tag in ("strong", "b"):
        inner = "".join(inline(c, ctx, depth + 1) for c in node.children).strip()
        return "**" + inner + "**" if inner else ""
    if tag in ("em", "i"):
        inner = "".join(inline(c, ctx, depth + 1) for c in node.children).strip()
        return "*" + inner + "*" if inner else ""
    if tag in ("del", "s"):
        inner = "".join(inline(c, ctx, depth + 1) for c in node.children).strip()
        return "~~" + inner + "~~" if inner else ""
    if tag == "a":
        label = "".join(inline(c, ctx, depth + 1) for c in node.children).strip()
        href = abs_url(ctx.url, node.attrs.get("href", ""))
        if href and label and not label.startswith("http"):
            return "[" + label + "](" + href + ")"
        return label
    if tag in ("ul", "ol"):
        return " " + " ".join(text_of(c) for c in node.children
                              if isinstance(c, Node) and c.tag == "li") + " "
    if tag == "p":
        inner = "".join(inline(c, ctx, depth + 1) for c in node.children).strip()
        return inner + " " if depth == 0 else inner
    if is_blocked(node):
        return ""
    return "".join(inline(c, ctx, depth + 1) for c in node.children)


def clean_inline(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([，。；：、！？）】」』,.;:!?)\]])", r"\1", text)
    text = re.sub(r"([（【「『])\s+", r"\1", text)
    return text.strip()


STANDALONE_LINK = re.compile(r"^\[[^\]]+\]\(https?://[^)]+\)$")


def is_breadcrumb(text):
    t = text.strip()
    return bool(STANDALONE_LINK.match(t)) and len(t) < 120


def is_boiler(text, min_len=10):
    t = text.strip()
    if len(t) <= 90 and BOILER_TEXT.match(t):
        return True
    if len(t) < min_len:
        return True
    return False


def flush_loose(ctx):
    text = clean_inline(" ".join(ctx.loose))
    ctx.loose = []
    if text and not is_boiler(text) and not is_breadcrumb(text):
        ctx.add(text)


def fence(code, lang=""):
    code = code.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not code.strip():
        return ""
    ticks = "```"
    while ticks in code:
        ticks += "`"
    return ticks + (lang or "text") + "\n" + code + "\n" + ticks


LANG_FROM_CLASS = re.compile(r"(?:brush:\s*|language-|lang-|sp-)([a-zA-Z0-9+#-]+)")


def detect_lang(pre):
    blob = pre.attrs.get("class", "").lower()
    code = next((c for c in pre.children if isinstance(c, Node) and c.tag == "code"), None)
    if code is not None:
        blob += " " + code.attrs.get("class", "").lower()
    for m in LANG_FROM_CLASS.finditer(blob):
        raw = m.group(1)
        if raw in LANG_ALIAS:
            return LANG_ALIAS[raw]
        if raw not in ("cm", "pristine", "pre"):
            return raw
    # TypeScript / shiki：<div class="language-id">ts</div>
    for node in pre.children:
        if isinstance(node, Node) and "language-id" in node.classes():
            raw = text_of(node).strip().lower()
            return LANG_ALIAS.get(raw, raw)
    return ""


def pre_text(pre):
    buf = []

    def walk(n):
        if isinstance(n, str):
            buf.append(n)
            return
        if n.tag in ("script", "style", "template", "svg"):
            return
        if "language-id" in n.classes():
            return
        if n.tag == "br":
            buf.append("\n")
            return
        line_box = n.tag == "div" and ("cm-line" in n.classes() or "line" in n.classes())
        if line_box and buf and not buf[-1].endswith("\n"):
            buf.append("\n")
        for c in n.children:
            walk(c)
        if line_box and (not buf or not buf[-1].endswith("\n")):
            buf.append("\n")

    for c in pre.children:
        walk(c)
    code = "".join(buf)
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    code = re.sub(r"\n{3,}", "\n\n", code)
    return code.strip("\n")


def iter_rows(table):
    """取本表自己的行（跳过嵌套表格）。"""
    rows = []

    def walk(n):
        for c in n.children:
            if not isinstance(c, Node):
                continue
            if c.tag == "table":
                continue
            if c.tag == "tr":
                rows.append(c)
                walk(c)
            else:
                walk(c)

    walk(table)
    return rows


def cell_text(cell):
    parts = []

    def walk(n):
        if isinstance(n, str):
            parts.append(n)
            return
        if n.tag in ("script", "style", "svg", "img"):
            return
        if n.tag == "br":
            parts.append(" ")
            return
        if n.tag == "li":
            parts.append("；")
        for c in n.children:
            walk(c)

    walk(cell)
    text = collapse("".join(parts)).lstrip("；")
    return text.replace("|", "/").replace("\n", " ")[:100]


def render_table(table):
    rows = []
    for tr in iter_rows(table):
        cells = [c for c in tr.children if isinstance(c, Node) and c.tag in ("th", "td")]
        vals = [cell_text(c) for c in cells]
        if any(vals):
            rows.append(vals)
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |",
             "|" + "|".join(["---"] * width) + "|"]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def list_text(li, ctx):
    """列表项自己的文字（嵌套列表单独处理）。"""
    parts = []
    for c in li.children:
        if isinstance(c, Node) and c.tag in ("ul", "ol", "pre", "table"):
            continue
        if isinstance(c, Node) and c.tag == "p":
            parts.append(inline(c, ctx, 1))
        else:
            parts.append(inline(c, ctx, 1))
    return clean_inline("".join(parts))


def render_list(node, ctx, depth=0):
    ordered = node.tag == "ol"
    lines = []
    idx = 0
    for li in node.children:
        if not isinstance(li, Node) or li.tag != "li":
            continue
        idx += 1
        text = list_text(li, ctx)
        marker = (str(idx) + ". ") if ordered else "- "
        prefix = "  " * depth
        if text:
            lines.append(prefix + marker + text)
        for sub in li.children:
            if isinstance(sub, Node) and sub.tag in ("ul", "ol"):
                sub_lines = render_list(sub, ctx, depth + 1)
                if sub_lines:
                    lines.extend(sub_lines)
        pre = next((c for c in li.children if isinstance(c, Node) and c.tag == "pre"), None)
        if pre is not None:
            body = fence(pre_text(pre), detect_lang(pre))
            if body:
                pad = "  " * (depth + 1)
                lines.append("\n".join(pad + ln if ln else ln for ln in body.split("\n")))
    return "\n".join(lines)


def walk_children(node, ctx):
    for c in node.children:
        walk(c, ctx)


def walk(node, ctx):
    if isinstance(node, str):
        text = re.sub(r"\s+", " ", node)
        if text.strip():
            ctx.loose.append(text)
        return

    tag = node.tag
    if tag in DROP_TAGS:
        return
    if is_blocked(node):
        return

    if tag in HEADING:
        level = HEADING[tag]
        text = clean_inline(text_of(node))
        if ctx.skip_level:
            if level <= ctx.skip_level:
                ctx.skip_level = 0
            else:
                return
        if level == 1:
            flush_loose(ctx)
            return
        if not text or BOILER_TEXT.match(text):
            flush_loose(ctx)
            return
        if DROP_SECTION.match(text):
            ctx.skip_level = level
            flush_loose(ctx)
            return
        flush_loose(ctx)
        ctx.add("#" * level + " " + text)
        return

    if ctx.skip_level:
        return

    if tag == "p":
        flush_loose(ctx)
        text = clean_inline(inline(node, ctx))
        if text and not is_boiler(text, 8) and not is_breadcrumb(text):
            ctx.add(text)
        return

    if tag == "pre":
        flush_loose(ctx)
        if "placeholder" in node.classes().lower():
            return
        body = fence(pre_text(node), detect_lang(node))
        if body:
            ctx.add(body)
        return

    if tag == "table":
        flush_loose(ctx)
        body = render_table(node)
        if body:
            ctx.add(body)
        return

    if tag in ("ul", "ol"):
        flush_loose(ctx)
        body = render_list(node, ctx)
        if body:
            ctx.add(body)
        return

    if tag == "blockquote":
        flush_loose(ctx)
        sub = Ctx(ctx.site, ctx.url)
        walk_children(node, sub)
        body = "\n\n".join(sub.blocks)
        if body:
            ctx.add("\n".join("> " + ln if ln else ">" for ln in body.split("\n")))
        return

    if tag == "hr":
        flush_loose(ctx)
        if ctx.blocks and ctx.blocks[-1].strip() != "---":
            ctx.add("---")
        return

    if tag == "dl":
        flush_loose(ctx)
        item = []
        for c in node.children:
            if isinstance(c, Node) and c.tag == "dt":
                item.append({"dt": clean_inline(text_of(c)), "dd": []})
            elif isinstance(c, Node) and c.tag == "dd" and item:
                item[-1]["dd"].append(clean_inline(inline(c, ctx)))
        for it in item:
            dd = "；".join(x for x in it["dd"] if x)
            if it["dt"]:
                ctx.add("- **" + it["dt"] + "**" + ("：" + dd if dd else ""))
        return

    if tag in ("figure", "figcaption", "details", "summary", "section", "article",
               "main", "div", "span", "center", "font", "li", "dd", "dt",
               "html", "body", "#document", "thead", "tbody",
               "tfoot", "tr", "td", "th", "colgroup", "col", "address", "time"):
        walk_children(node, ctx)
        return

    # text 级别的其它标签：当作行内
    text = inline(node, ctx)
    if text.strip():
        ctx.loose.append(text)


# ---------------------------------------------------------------------------
# 网络：robots、限速、重试
# ---------------------------------------------------------------------------
class Fetcher:
    def __init__(self, interval):
        self.interval = max(MIN_INTERVAL, float(interval or 0))
        self.last = {}
        self.robots = {}

    def _wait(self, host):
        prev = self.last.get(host)
        if prev is not None:
            gap = time.time() - prev
            if gap < self.interval:
                time.sleep(self.interval - gap)
        self.last[host] = time.time()

    def allowed(self, url):
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc.lower()
        if host not in self.robots:
            self.robots[host] = self._load_robots(parts)
        parser = self.robots[host]
        return parser.can_fetch(UA, url) if parser is not None else True

    def _load_robots(self, parts):
        robots_url = parts.scheme + "://" + parts.netloc + "/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        self._wait(parts.netloc.lower())
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
            text = raw.decode("utf-8", "ignore")
            parser.parse(text.splitlines())
        except Exception:
            parser.allow_all = True
        return parser

    def get(self, url):
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc.lower()
        if not self.allowed(url):
            raise RuntimeError("robots.txt 禁止抓取")
        last_err = None
        for attempt in range(MAX_RETRY + 1):
            self._wait(host)
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    raw = resp.read()
                    charset = resp.headers.get_content_charset()
                return decode(raw, charset)
            except urllib.error.HTTPError as exc:
                last_err = "HTTP %s" % exc.code
                if exc.code in (403, 404, 410, 429, 500, 502, 503):
                    break
            except (socket.timeout, TimeoutError):
                last_err = "超时"
            except urllib.error.URLError as exc:
                reason = str(exc.reason)
                last_err = "连接失败：" + reason[:40] if reason else "连接失败"
            except Exception as exc:  # 其它网络异常
                last_err = type(exc).__name__ + "：" + str(exc)[:40]
            if attempt < MAX_RETRY:
                time.sleep(self.interval * (attempt + 1))
        raise RuntimeError(last_err or "抓取失败")


def decode(raw, charset):
    if charset:
        try:
            return raw.decode(charset, "replace")
        except LookupError:
            pass
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


# ---------------------------------------------------------------------------
# 正文组装
# ---------------------------------------------------------------------------
def extract_body(html, url):
    doc = parse_html(html)
    root, site = pick_root(doc, url)
    if root is None:
        return None, site, "", "未识别正文容器"
    ctx = Ctx(site, url)
    walk_children(root, ctx)
    flush_loose(ctx)
    body = "\n\n".join(ctx.blocks).strip()
    return body, site, meta_descriptions(doc), ""


CJK = re.compile(r"[一-鿿]")


def build_summary(descs, body, title):
    body_cjk = len(CJK.findall(body)) > 200
    ranked = [d for d in descs if len(d) >= 25 and not BOILER_TEXT.match(d)]
    if body_cjk:
        ranked = [d for d in ranked if CJK.search(d)]
    elif ranked:
        same = [d for d in ranked if not CJK.search(d)]
        ranked = same or ranked
    if ranked:
        return ranked[0][:110]
    for block in body.split("\n\n"):
        line = block.strip()
        if not line or line.startswith(("#", "|", "```", ">", "---")):
            continue
        plain = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        plain = re.sub(r"[*_`~]", "", plain).strip()
        if len(plain) >= 25:
            return plain[:110]
    return (title + "：原文正文摘录，包含标题层级、代码示例与要点整理。")[:110]


def truncate_body(body, limit):
    if limit <= 0 or len(re.sub(r"\s", "", body)) <= limit:
        return body, False
    blocks, used, kept = body.split("\n\n"), 0, []
    budget = max(200, limit - 160)
    for block in blocks:
        size = len(re.sub(r"\s", "", block))
        if used + size > budget:
            break
        kept.append(block)
        used += size
    tail = "\n\n（正文过长，此处为节选；完整内容请访问原文。）"
    return "\n\n".join(kept).rstrip() + tail, True


def write_entry(root, item, body, summary):
    text_len = len(re.sub(r"\s", "", body))
    minutes = max(5, min(45, round(text_len / 450) or 5))
    fm = "\n".join([
        "---",
        "slug: " + item["stack"] + "/" + item["category"] + "/" + item["slug"],
        'title: "' + item["title"].replace('"', "'") + '"',
        'summary: "' + summary.replace('"', "'") + '"',
        "author: " + item["author"],
        "license: " + item["license"],
        "source_url: " + item["url"],
        "stack: " + item["stack"],
        "category: " + item["category"],
        "level: beginner",
        "type: docs",
        "duration_min: " + str(minutes),
        "tags: [" + ", ".join(item["tags"]) + "]",
        "lang: zh",
        "---",
        "",
    ])
    note = ("\n\n> 本文正文摘录自 [原文](" + item["url"] + ")，版权归 " + item["author"]
            + " 所有（" + item["license"] + "）；完整内容与更新请访问原文。")
    content = fm + "\n" + body + note + "\n"
    d = root / item["stack"] / item["category"]
    d.mkdir(parents=True, exist_ok=True)
    (d / (item["slug"] + ".md")).write_text(content, encoding="utf-8", newline="\n")
    return text_len


def body_chars_of(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    parts = text.split("---", 2)
    body = parts[2] if len(parts) == 3 else text
    return len(re.sub(r"\s", "", body))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def load_registry(path=REGISTRY):
    if not path.exists():
        raise SystemExit("找不到目标清单: " + str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./content")
    ap.add_argument("--batch", default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    reg = load_registry()
    batches = reg["batches"]
    rules = reg.get("rules", {})

    if args.list:
        for name, items in batches.items():
            print("%-6s %2d 篇" % (name, len(items)))
            for it in items:
                print("   ", it["slug"], "->", it["url"])
        return

    targets = []
    for name, items in batches.items():
        if args.batch in ("all", name):
            targets.extend(items)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        targets = [t for t in targets if t["slug"] in wanted]
    if not targets:
        raise SystemExit("没有匹配的目标")

    if rules.get("only_summary"):
        print("提示: only_summary 已废弃，本脚本始终按完整正文模式抓取")
    max_body = int(rules.get("max_body_chars", 20000) or 0)
    fetcher = Fetcher(rules.get("fetch_interval_sec", MIN_INTERVAL))
    root = Path(args.out)
    report, ok = [], 0

    for item in targets:
        path = root / item["stack"] / item["category"] / (item["slug"] + ".md")
        old = body_chars_of(path)
        row = {"slug": item["slug"], "url": item["url"], "old": old,
               "new": 0, "result": "失败", "reason": ""}
        try:
            html = fetcher.get(item["url"])
        except Exception as exc:
            row["reason"] = str(exc)
            print("FAIL", item["slug"], row["reason"])
            report.append(row)
            continue

        body, site, descs, err = extract_body(html, item["url"])
        size = len(re.sub(r"\s", "", body or ""))
        if err or not body or size < 200:
            row["reason"] = err or "正文过短（提取结果 %d 字）" % size
            print("FAIL", item["slug"], row["reason"])
            report.append(row)
            continue

        body, truncated = truncate_body(body, max_body)
        summary = build_summary(descs, body, item["title"])
        new = write_entry(root, item, body, summary)
        row.update(new=new, result="成功", site=site,
                   reason=("正文超过 %d 字，已按规则截断" % max_body) if truncated else "")
        print("OK", item["slug"], old, "->", new, "字", "（截断）" if truncated else "")
        report.append(row)
        ok += 1

    print("完成:", ok, "/", len(targets))
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
