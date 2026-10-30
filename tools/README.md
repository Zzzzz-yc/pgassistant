# tools/ — 内容维护脚本

这些脚本只在**维护内容**时用，不参与应用运行。

| 脚本 | 作用 | 什么时候跑 |
|---|---|---|
| `convert_content.py` | hello-algo 章节 → 本项目格式（front-matter、code-group、资源路径） | 内容源更新时 |
| `split_jwasham.py` | jwasham 大单文件 → 16 篇 | 同上 |
| `fetch_intro.py` | 按 sources.json 抓外站教程的摘要/要点/速查表 | 补内容时 |
| `optimize_media.py` | 超 300KB 图片转 WebP 并改写引用 | 加了图片之后 |
| `sources.json` | **抓取目标的唯一事实来源**，新增内容只改这里 | 每次补内容 |
| `conversion-report.md` | 转换过程与质量记录 | 归档 |

## 典型流程

```
补内容:  改 sources.json → fetch_intro.py → validate_content.py → bootstrap_index.py → content_gaps.py
换图片:  放入 content/assets → optimize_media.py → validate_content.py
改内容源: convert_content.py / split_jwasham.py → validate_content.py → bootstrap_index.py
```

## 依赖

- Python 3.11+
- jieba（仅索引与检索需要，脚本本身不需要）
- Pillow（仅 optimize_media.py 需要）

依赖装在 `.deps/`（被 .gitignore 忽略），用 `pip install --target .deps <包>` 恢复。
