# tools/ — 内容维护脚本

这些脚本只在**维护内容**时用，不参与应用运行。

| 脚本 | 作用 | 什么时候跑 |
|---|---|---|
| `convert_content.py` | hello-algo 章节 → 本项目格式（front-matter、code-group、资源路径） | 内容源更新时 |
| `split_jwasham.py` | jwasham 大单文件 → 16 篇 | 同上 |
| `fetch_intro.py` | 按 sources.json 抓外站教程的**正文主体**，生成项目格式 Markdown | 补内容时 |
| `optimize_media.py` | 超 300KB 图片转 WebP 并改写引用 | 加了图片之后 |
| `sources.json` | **抓取目标的唯一事实来源**，新增内容只改这里 | 每次补内容 |
| `conversion-report.md` | 转换过程与质量记录 | 归档 |

## 抓取规则（sources.json 的 `rules`）

```json
"rules": {"only_summary": false, "max_body_chars": 20000, "fetch_interval_sec": 1.0}
```

| 键 | 含义 |
|---|---|
| `only_summary` | 已废弃的旧开关。固定为 `false`：提取完整正文，不再只取「摘要 + 前 5 段要点 + 速查表」。置为 `true` 时脚本只打印提示，仍按完整正文处理 |
| `max_body_chars` | 单篇正文上限（去空白字符数）。超过时按段落边界截断，并在文末标注「节选」 |
| `fetch_interval_sec` | 同一域名的请求间隔，**不得小于 1.0 秒**（脚本内强制取 `max(1.0, 配置值)`） |

`fetch_intro.py` 的抓取约束：

- 遵守各站 `robots.txt`；被禁止的 URL 直接失败并记录原因，不绕过。
- 同一域名串行请求（脚本单线程），间隔 ≥1 秒；失败重试 2 次，指数退避。
- 纯标准库：`urllib` + `html.parser`，不引第三方 HTML 解析库。
- 正文提取保留 h2/h3 标题层级、代码块（含语言标记）、表格、列表；剔除导航、页脚、侧栏、广告、评论区、「本页译自」类样板。
- 已适配页面结构：`runoob.com`（`#content` / `.article-intro`）、`developer.mozilla.org`（`main#content`）、`zh-hans.react.dev`（`article`）、`typescriptlang.org`（`article .markdown`）；其它站点走 `<main>`/`<article>` 兜底。
- 输出：front-matter 必填字段与 `license` 不变，`body_md` 统一 LF、UTF-8 无 BOM；正文末尾保留 `source_url` 与许可的引用说明。

## 典型流程

```
补内容:  改 sources.json → fetch_intro.py → validate_content.py → bootstrap_index.py → content_gaps.py
换图片:  放入 content/assets → optimize_media.py → validate_content.py
改内容源: convert_content.py / split_jwasham.py → validate_content.py → bootstrap_index.py
```

`fetch_intro.py` 常用参数：

```
python tools/fetch_intro.py --out ./content --batch p2
python tools/fetch_intro.py --out ./content --batch all --only js-closures,sql-intro
python tools/fetch_intro.py --list
python tools/fetch_intro.py --out ./content --batch all --only ... --report report.json
```

## 依赖

- Python 3.11+
- jieba（仅索引与检索需要，脚本本身不需要）
- Pillow（仅 optimize_media.py 需要）

依赖装在 `.deps/`（被 .gitignore 忽略），用 `pip install --target .deps <包>` 恢复。
