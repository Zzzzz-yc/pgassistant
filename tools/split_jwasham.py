#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
split_jwasham.py — 把 jwasham 的 README-cn.md 按主题拆成多篇资料

为什么不是一段一篇：35 个二级标题里多数是链接清单，单段 100-400 字符，
过不了 §4.5 的准入标准（正文 ≥400 字）。按主题归并成 17 篇后每篇都有实质内容。

用法:
  python tools/split_jwasham.py --src D:/githubrepotest/curated-content/jwasham/translations/README-cn.md --out ./content
"""
import argparse
import re
from pathlib import Path

# (slug, 标题, [要合并进来的二级标题])
GROUPS = [
    ("guide", "面试准备指南：怎么用这份清单", [
        "这是？", "为何要用到它？", "如何使用它", "拥有一名 Googler 的心态",
        "我得到了工作吗？", "跟着我的脚步", "不要妄自菲薄", "关于 Google",
    ]),

    ("interview-process", "面试流程与准备工作", [
        "面试过程 & 通用的面试准备", "为你的面试选择一种语言",
        "在你开始之前", "你所看不到的", "日常计划",
    ]),
    ("prerequisite", "必备知识", ["必备知识"]),
    ("complexity", "算法复杂度：Big-O 与渐进分析", ["算法复杂度 / Big-O / 渐进分析法"]),
    ("data-structures", "数据结构：数组、链表、栈、队列、哈希", ["数据结构"]),
    ("trees", "树结构", ["树（Trees）"]),
    ("sorting", "排序算法", ["排序（Sorting）"]),
    ("graphs", "图与图算法", ["图（Graphs）"]),
    ("advanced", "进阶知识", ["更多的知识", "更多知识"]),
    ("final-interview", "终面与系统设计", ["终面"]),
    ("books", "书单", ["书籍"]),
    ("practice", "编码练习与挑战", ["编码练习和挑战"]),
    ("before-interview", "面试前后：简历、现场与提问", [
        "当你临近面试时", "你的简历", "当面试来临的时候",
        "问面试官的问题", "当你获得了梦想的职位",
    ]),
    ("extra-topics", "附加学习主题", ["附加的学习", "一些主题的额外内容"]),
    ("video-series", "视频与课程资源", ["相关视频资源", "视频系列", "计算机科学课程"]),
]

FENCE = "`" * 3
H2 = re.compile(r'^##\s+(.+?)\s*$')


def split_sections(text: str):
    """按二级标题切分，返回 [(标题, 正文)]，跳过「目录」"""
    lines = text.splitlines()
    out, cur_title, buf = [], None, []
    for line in lines:
        m = H2.match(line)
        if m:
            if cur_title is not None:
                out.append((cur_title, "\n".join(buf).strip()))
            cur_title, buf = m.group(1).strip(), []
        else:
            if cur_title is not None:
                buf.append(line)
    if cur_title is not None:
        out.append((cur_title, "\n".join(buf).strip()))
    return [(t, b) for t, b in out if t != "目录"]


def unlink_anchors(text: str) -> str:
    """把 [文字](#锚点) 降级成普通文字，避免拆分后断链"""
    return re.sub(r'\[([^\]]+)\]\(#[^)]*\)', r'\1', text)


def plain(text: str) -> str:
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    t = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', t)
    t = re.sub(r'[:*`<>]', '', t)
    t = re.sub(r'^\s*[-*]\s*(\[[ xX]\])?\s*', '', t, flags=re.M)
    t = t.replace("[ ]", "").replace("[x]", "")
    return re.sub(r'\s+', ' ', t).strip()


def first_paragraph(body: str) -> str:
    for block in body.split("\n\n"):
        head = block.lstrip()[:1]
        if head in ("-", "*", "|", ">", "#", "!"):
            continue
        s = plain(block)
        if len(s) >= 25 and not s.startswith("http"):
            return s[:110]
    return ""


def estimate_minutes(body: str) -> int:
    cjk = len(re.findall(r'[\u4e00-\u9fff]', body))
    words = len(re.findall(r'[A-Za-z]{2,}', body))
    # 链接清单阅读速度快，按更低的密度折算
    return max(5, min(90, round(cjk / 500 + words / 300)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    text = Path(args.src).read_text(encoding="utf-8")
    sections = dict(split_sections(text))
    missing = [t for _, _, titles in GROUPS for t in titles if t not in sections]
    if missing:
        print("警告：以下标题没在原文中找到 ->", missing)

    out_dir = Path(args.out) / "cs-basics" / "interview"
    out_dir.mkdir(parents=True, exist_ok=True)

    index_rows = []
    total = 0
    for order, (slug, title, titles) in enumerate(GROUPS, start=1):
        chunks = []
        for t in titles:
            body = sections.get(t)
            if body:
                chunks.append("## " + t + "\n\n" + body)
        body = unlink_anchors("\n\n".join(chunks))
        if not body.strip():
            continue

        if slug != "guide":
            title = "面试清单：" + title
        links = body.count("](http")
        summary = first_paragraph(body) or (
            title + "——jwasham 计算机面试准备清单节选，共收录 " + str(links) + " 个学习资源链接。"
        )
        minutes = estimate_minutes(body)
        kind = "overview" if slug == "guide" else "docs"

        fm = "\n".join([
            "---",
            "slug: cs-basics/interview/" + slug,
            'title: "' + title + '"',
            'summary: "' + summary.replace('"', "'") + '"',
            "author: John Washam",
            "license: MIT",
            "source_url: https://github.com/jwasham/coding-interview-university/blob/master/translations/README-cn.md",
            "stack: cs-basics",
            "category: interview",
            "level: beginner",
            "type: " + kind,
            "duration_min: " + str(minutes),
            "tags: [面试, 计算机基础]",
            "lang: zh",
            "---",
            "",
        ])
        (out_dir / (slug + ".md")).write_text(fm + "\n" + body + "\n", encoding="utf-8", newline="\n")
        index_rows.append((order, title, slug, minutes))
        total += 1

    # 附带的语言资源清单（原文 ../programming-language-resources.md，被 README 引用）
    lang_src = Path(args.src).parent.parent / "programming-language-resources.md"
    if lang_src.exists():
        raw = lang_src.read_text(encoding="utf-8")
        body = re.sub(r'^#+\s*Programming Language Resources\s*$', '', raw, flags=re.M).strip()
        body = unlink_anchors(body)
        minutes = estimate_minutes(body)
        fm = "\n".join([
            "---",
            "slug: cs-basics/interview/programming-language-resources",
            'title: "面试清单：编程语言学习资源"',
            'summary: "按语言整理的面试准备资源：C / C++ / Python / Java / Go / JavaScript 的速查表、教程与视频。"',
            "author: John Washam",
            "license: MIT",
            "source_url: https://github.com/jwasham/coding-interview-university/blob/master/programming-language-resources.md",
            "stack: cs-basics",
            "category: interview",
            "level: beginner",
            "type: docs",
            "duration_min: " + str(minutes),
            "tags: [面试, 编程语言]",
            "lang: zh",
            "---",
            "",
        ])
        (out_dir / "programming-language-resources.md").write_text(fm + "\n" + body + "\n", encoding="utf-8", newline="\n")
        total += 1
        index_rows.append((len(index_rows) + 1, "面试清单：编程语言学习资源", "programming-language-resources", minutes))

    for md in out_dir.glob("*.md"):
        t = md.read_text(encoding="utf-8")
        if "](../programming-language-resources.md)" in t:
            md.write_text(t.replace("](../programming-language-resources.md)", "](./programming-language-resources.md)"), encoding="utf-8", newline="\n")

    print("拆分完成：" + str(total) + " 篇 -> " + str(out_dir))
    for o, t, s, m in index_rows:
        print("  {:02d}. {:<34} {} ({}min)".format(o, t, s + ".md", m))

if __name__ == "__main__":
    main()