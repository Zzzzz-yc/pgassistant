#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
convert_content.py — 把 hello-algo / jwasham 的原始 Markdown 转成本项目的 content/ 规范

用法:
  python tools/convert_content.py --src D:/githubrepotest/curated-content --out ./content_sample --only chapter_array_and_linkedlist

转换规则:
  1. 生成 YAML front-matter（title/summary/stack/category/level/type/duration_min/tags/slug）
  2. === "Python" 代码页  ->  ::: code-group 指令（前端用 remark 插件渲染成标签页）
  3. !!! note 提示块      ->  引用块
  4. ??? "标题" 折叠块    ->  引用块
  5. 保留 $..$ / $$..$$ 公式与相对图片路径，并同步拷贝 *.assets/ 目录
"""
import argparse
import re
import shutil
from pathlib import Path

FENCE = "`" * 3

CHAPTERS = {
    "chapter_introduction":             ("入门",        "introduction",        "beginner"),
    "chapter_computational_complexity": ("复杂度分析",  "complexity",          "beginner"),
    "chapter_data_structure":           ("数据结构",    "data-structure",      "beginner"),
    "chapter_array_and_linkedlist":     ("数组与链表",  "array-linkedlist",    "beginner"),
    "chapter_stack_and_queue":          ("栈与队列",    "stack-queue",         "beginner"),
    "chapter_hashing":                  ("哈希表",      "hashing",             "beginner"),
    "chapter_tree":                     ("树",          "tree",                "intermediate"),
    "chapter_heap":                     ("堆",          "heap",                "intermediate"),
    "chapter_graph":                    ("图",          "graph",               "intermediate"),
    "chapter_searching":                ("搜索",        "searching",           "beginner"),
    "chapter_sorting":                  ("排序",        "sorting",             "intermediate"),
    "chapter_divide_and_conquer":       ("分治",        "divide-conquer",      "intermediate"),
    "chapter_backtracking":             ("回溯",        "backtracking",        "intermediate"),
    "chapter_dynamic_programming":      ("动态规划",    "dynamic-programming", "advanced"),
    "chapter_greedy":                   ("贪心",        "greedy",              "intermediate"),
}

# 可站内运行（WASM）的章节：仅算法类。front-matter 据此写 runnable 字段（§20.1）
RUNNABLE_DIRS = {
    "chapter_computational_complexity", "chapter_array_and_linkedlist",
    "chapter_stack_and_queue", "chapter_hashing", "chapter_tree",
    "chapter_heap", "chapter_graph", "chapter_searching",
    "chapter_sorting", "chapter_divide_and_conquer",
    "chapter_backtracking", "chapter_dynamic_programming",
    "chapter_greedy",
}

# 文件名约定 -> type（§4.6 枚举）
TYPE_BY_STEM = {"index": "overview", "summary": "summary", "exercises": "exercise"}

TAB_RE = re.compile(r'^===\s+"(.+?)"\s*$')
ADMON_RE = re.compile(r'^!!!\s+(\w+)(?:\s+"(.+)")?\s*$')
H1_RE = re.compile(r'^#\s+(.+?)\s*$')


def is_indented(line: str) -> bool:
    return line.startswith("    ")


def take_indented(lines, i):
    block = []
    n = len(lines)
    while i < n:
        line = lines[i]
        if is_indented(line):
            block.append(line[4:])
            i += 1
        elif line.strip() == "":
            block.append("")
            i += 1
        else:
            break
    while block and block[-1].strip() == "":
        block.pop()
    return block, i


def strip_md(text: str) -> str:
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace("**", "").replace("*", "").replace("`", "")
    text = re.sub(r'\$([^$]*)\$', r'\1', text)
    text = text.replace(chr(92), '')  # 去掉 LaTeX 转义反斜杠
    return re.sub(r'\s+', ' ', text).strip()

def convert_body(lines):
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        m = TAB_RE.match(line)
        if m:
            groups = []
            while i < n and TAB_RE.match(lines[i]):
                name = TAB_RE.match(lines[i]).group(1).strip()
                i += 1
                while i < n and lines[i].strip() == "":
                    i += 1
                block, i = take_indented(lines, i)
                groups.append((name, block))
            out.append("::: code-group")
            out.append("")
            for name, block in groups:
                body = "\n".join(block)
                body = re.sub(
                    r'^```(\w+)\s+title="[^"]*"',
                    lambda m: FENCE + m.group(1) + ' title="' + name + '"',
                    body,
                    flags=re.M,
                )
                if not body.lstrip().startswith(FENCE):
                    out.append('```text title="' + name + '"')
                    out.append(body)
                    out.append(FENCE)
                else:
                    out.append(body)
                out.append("")
            out.append(":::")
            out.append("")
            continue

        m = ADMON_RE.match(line)
        if m:
            kind, title = m.group(1), (m.group(2) or "")
            block, i = take_indented(lines, i + 1)
            while block and block[0].strip() == "":
                block.pop(0)
            head = "**" + kind.upper() + (" · " + title if title else "") + "**"
            out.append("> " + head)
            out.append(">")
            for b in block:
                out.append("> " + b if b.strip() else ">")
            out.append("")
            continue

        if line.startswith("???"):
            tm = re.search(r'"(.*?)"', line)
            title = tm.group(1).strip() if tm else "展开"
            block, i = take_indented(lines, i + 1)
            out.append("> **" + title + "**")
            out.append(">")
            for b in block:
                out.append("> " + b if b.strip() else ">")
            out.append("")
            continue

        out.append(line)
        i += 1
    return out


def estimate_minutes(text: str) -> int:
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
    words = len(re.findall(r'[A-Za-z]+', text))
    minutes = round(cjk / 380 + words / 220)
    return max(5, min(120, minutes))


def yaml_escape(s: str) -> str:
    s = s.replace('\\', '\\\\').replace('"', '\\"')
    return '"' + s + '"'


def build_front_matter(meta, title, summary, minutes, tags):
    fm = [
        "---",
        "slug: " + meta["slug"],
        "title: " + yaml_escape(title),
        "summary: " + yaml_escape(summary),
        "stack: " + meta["stack"],
        "category: " + meta["category"],
        "level: " + meta["level"],
        "type: " + meta["type"],
        "duration_min: " + str(minutes),
        "tags: [" + ", ".join(tags) + "]",
        "author: " + yaml_escape(meta["author"]),
        "license: " + meta["license"],
        "source_url: " + yaml_escape(meta["source_url"]),
        "lang: zh",
    ]
    if meta.get("runnable"):
        fm.append("runnable: [" + ", ".join(meta["runnable"]) + "]")
    fm.extend(["---", ""])
    return fm


def convert_file(src: Path, dst_dir: Path, meta: dict, dry=False):
    raw = src.read_text(encoding="utf-8")
    lines = raw.splitlines()

    title = ""
    body_start = 0
    for idx, line in enumerate(lines):
        m = H1_RE.match(line)
        if m:
            title = m.group(1).strip()
            body_start = idx + 1
            break

    # 标题去重/去泛化：导读页与"练习/小结"类页面加章节前缀
    if meta["type"] == "overview":
        title = meta["category_cn"] + " · 章节导读"
    elif title in ("小结", "练习", "习题", "总结", "本章小结", "练习题"):
        title = meta["category_cn"] + " · " + title

    converted = convert_body(lines[body_start:])
    while converted and converted[0].strip() == "":
        converted.pop(0)

    summary = ""
    for line in converted:
        s = line.strip()
        if not s or s[0] in "#!>:|-*" or s.startswith(":::"):
            continue
        if s.startswith(FENCE):
            continue
        summary = strip_md(s)
        if len(summary) >= 20:
            break
    if len(summary) < 25:
        heads = [strip_md(x[3:]) for x in converted if x.startswith("## ")]
        heads = [h for h in heads if h][:3]
        if heads:
            summary = (summary + " " if summary else "") + "本页涵盖：" + "、".join(heads)
    summary = summary[:120]
    if len(summary) < 25:
        kind = {"overview": "章节导读", "summary": "章节小结", "exercise": "练习题"}.get(meta["type"], "学习资料")
        summary = kind + "：" + meta["category_cn"]

    minutes = estimate_minutes("\n".join(converted))

    tags = [meta["category_cn"], meta["level"]]
    for line in converted:
        if line.startswith("## "):
            t = strip_md(line[3:])
            if t and len(t) <= 12 and t not in tags:
                tags.append(t)
        if len(tags) >= 6:
            break

    fm = build_front_matter(meta, title or src.stem, summary or (title or src.stem), minutes, tags)
    out_text = "\n".join(fm + converted).rstrip() + "\n"
    out_text = out_text.replace("](../assets/", "](../../assets/")

    dst = dst_dir / (src.stem + ".md")
    if not dry:
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text(out_text, encoding="utf-8", newline="\n")

    assets_src = src.parent / (src.stem + ".assets")
    copied_assets = 0
    if assets_src.is_dir():
        assets_dst = dst_dir / (src.stem + ".assets")
        if not dry:
            if assets_dst.exists():
                shutil.rmtree(assets_dst, ignore_errors=True)
            shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)
        copied_assets = sum(1 for _ in assets_src.rglob("*") if _.is_file())

    return {
        "file": src.name,
        "title": title,
        "minutes": minutes,
        "chars": len(out_text),
        "assets": copied_assets,
        "tabs": raw.count('=== "'),
        "admon": len(re.findall(r'^!!!\s', raw, flags=re.M)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src_root = Path(args.src) / "hello-algo" / "docs"
    out_root = Path(args.out)
    if not src_root.is_dir():
        raise SystemExit("源目录不存在: " + str(src_root))

    # 全局资源（章节封面等）统一放 content/assets/，正文里已重写为 ../../assets/
    shared_src = src_root / "assets"
    if shared_src.is_dir() and not args.dry_run:
        shared_dst = out_root / "assets"
        if shared_dst.exists():
            shutil.rmtree(shared_dst, ignore_errors=True)
        shutil.copytree(
            shared_src,
            shared_dst,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.css", "*.js", "*.html", "*.pdf"),
        )

    report = []
    for d in sorted(src_root.iterdir()):
        if not d.is_dir() or d.name not in CHAPTERS:
            continue
        if args.only and d.name != args.only:
            continue
        cn, slug, level = CHAPTERS[d.name]
        dst_dir = out_root / "cs-basics" / slug
        for md in sorted(d.glob("*.md")):
            meta = {
                "stack": "cs-basics",
                "category": slug,
                "category_cn": cn,
                "level": level,
                "slug": "cs-basics/" + slug + "/" + md.stem,
                "author": "krahets",
                "license": "MIT",
                "source_url": "https://github.com/krahets/hello-algo/blob/main/docs/" + d.name + "/" + md.name,
                "runnable": ["python", "javascript"] if d.name in RUNNABLE_DIRS else None,
                "type": TYPE_BY_STEM.get(md.stem, "article"),
            }
            r = convert_file(md, dst_dir, meta, dry=args.dry_run)
            r["chapter"] = cn
            report.append(r)

    print("转换文件: " + str(len(report)) + " | 拷贝资源文件: " + str(sum(r["assets"] for r in report)))
    for r in report:
        print("  {:<16} {:>3}min {:>6}B  tabs:{:<3} admon:{:<2} assets:{}".format(
            r["file"], r["minutes"], r["chars"], r["tabs"], r["admon"], r["assets"]))
    left = 0
    for f in out_root.rglob("*.md"):
        t = f.read_text(encoding="utf-8")
        left += len(re.findall(r'^===\s+"', t, flags=re.M)) + len(re.findall(r'^!!!\s', t, flags=re.M))
    print("残留 mkdocs 语法:", left)
    if not args.dry_run:
        write_credits(out_root, len(report))
        print("已生成 CREDITS.md")


def write_credits(out_root: Path, total: int):
    text = "\n".join([
        "# 内容来源与许可（CREDITS）",
        "",
        "本目录下的学习资料来自以下开源项目，按 MIT 许可使用。",
        "每篇资料的 front-matter 中记录了 author / license / source_url。",
        "",
        "| 上游项目 | 作者 | 许可 | 来源 | 收录范围 |",
        "|---|---|---|---|---|",
        "| hello-algo | krahets | MIT | https://github.com/krahets/hello-algo | 算法与数据结构章节，共 " + str(total) + " 篇 |",
        "| coding-interview-university | John Washam | MIT | https://github.com/jwasham/coding-interview-university | 面试路线中文版与速查表 |",
        "",
        "MIT 许可要求保留原始版权声明：完整许可证见各来源目录下的 LICENSE 文件。",
        "如果你要二次分发，请连同本文件与 LICENSE 一起带走。",
        "",
    ])
    (out_root / "CREDITS.md").write_text(text, encoding="utf-8", newline="\n")

if __name__ == "__main__":
    main()
