#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_intro.py — 抓取入门类教程的摘要、要点段落与速查表

只取「标题 + 摘要 + 前几段要点 + 速查表 + 原文链接」，不整篇搬运，属引用性使用。

目标清单在 tools/sources.json，**新增内容只改 JSON，不改代码**：
  {
    "batches": {
      "p2": [
        {"slug":"js-vite","title":"Vite 入门","url":"https://...","stack":"frontend",
         "category":"tooling","author":"vitejs","license":"MIT","tags":["前端","构建"]}
      ]
    }
  }

用法:
  python tools/fetch_intro.py --out ./content --batch p2
  python tools/fetch_intro.py --out ./content --batch p1 --only npm-intro
  python tools/fetch_intro.py --list            # 看清单现状
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

REGISTRY = Path(__file__).with_name("sources.json")
BOILER = re.compile(r"^(View in English|Always switch|跳到|Skip to|切换语言|本页译自|菜鸟教程|本站)")


class Extractor(HTMLParser):
    """收集段落与表格；参考类页面正文几乎全在 table 里。"""

    def __init__(self):
        super().__init__()
        self.skip = 0
        self.in_p = False
        self.buf = []
        self.paras = []
        self.in_table = False
        self.in_cell = False
        self.cell = []
        self.row = []
        self.rows = []
        self.tables = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self.skip += 1
        elif tag == "p":
            self.in_p, self.buf = True, []
        elif tag == "table":
            self.in_table, self.rows = True, []
        elif tag == "tr" and self.in_table:
            self.row = []
        elif tag in ("td", "th") and self.in_table:
            self.in_cell, self.cell = True, []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header") and self.skip > 0:
            self.skip -= 1
        elif tag == "p" and self.in_p:
            t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if len(t) >= 40:
                self.paras.append(t)
            self.in_p = False
        elif tag in ("td", "th") and self.in_cell:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.in_cell = False
        elif tag == "tr" and self.in_table:
            if len(self.row) >= 2 and any(self.row):
                self.rows.append(self.row)
            self.row = []
        elif tag == "table" and self.in_table:
            if len(self.rows) >= 2:
                self.tables.append(self.rows)
            self.in_table = False

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)
        elif self.in_p and self.skip == 0:
            self.buf.append(data)


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; pgassistant-content-bot/1.0)",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", ignore)


def md_table(rows, max_rows=20):
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]

    def cell(v):
        return v.replace("|", "/").replace("\n", " ")[:60]

    lines = ["| " + " | ".join(cell(c) for c in norm[0]) + " |",
             "|" + "|".join(["---"] * width) + "|"]
    for r in norm[1:max_rows]:
        if any(r):
            lines.append("| " + " | ".join(cell(c) for c in r) + " |")
    return "\n".join(lines)


def load_registry(path=REGISTRY):
    if not path.exists():
        raise SystemExit("找不到目标清单: " + str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def build_body(paras, table_md, url, author):
    parts = ["## 概要", "", paras[0] if paras else "", ""]
    if len(paras) > 1:
        parts += ["## 要点", "", "\n\n".join(paras[1:6]), ""]
    if table_md:
        parts += ["## 速查表", "", table_md, ""]
    body = "\n".join(parts).strip()
    return body + "\n\n> 本文为原文摘要与要点摘录，完整内容请访问 [原文](" + url + ")。版权归 " + author + " 所有。"


def write_entry(root, item, paras, table_md, summary):
    fm = "\n".join([
        "---",
        "slug: " + item["stack"] + "/" + item["category"] + "/" + item["slug"],
        'title: "' + item["title"] + '"',
        'summary: "' + summary.replace('"', "'") + '"',
        "author: " + item["author"],
        "license: " + item["license"],
        "source_url: " + item["url"],
        "stack: " + item["stack"],
        "category: " + item["category"],
        "level: beginner",
        "type: docs",
        "duration_min: " + str(max(5, min(40, round(len("\n".join(paras)) / 700 * 5) or 5))),
        "tags: [" + ", ".join(item["tags"]) + "]",
        "lang: zh",
        "---",
        "",
    ])
    d = root / item["stack"] / item["category"]
    d.mkdir(parents=True, exist_ok=True)
    (d / (item["slug"] + ".md")).write_text(
        fm + "\n" + build_body(paras, table_md, item["url"], item["author"]) + "\n",
        encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./content")
    ap.add_argument("--batch", default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    reg = load_registry()
    batches = reg["batches"]

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

    root = Path(args.out)
    ok = 0
    for item in targets:
        try:
            html = fetch(item["url"])
        except Exception as exc:
            print("FAIL", item["slug"], str(exc)[:60])
            continue

        ex = Extractor()
        ex.feed(html)

        paras, seen = [], set()
        for t in ex.paras:
            if BOILER.match(t):
                continue
            if t[:30] in seen:
                continue
            seen.add(t[:30])
            paras.append(t)
            if sum(len(x) for x in paras) > 900:
                break

        tables = sorted(ex.tables, key=len, reverse=True)
        table_md = md_table(tables[0]) if tables else ""
        if not paras and not table_md:
            print("EMPTY", item["slug"])
            continue

        summary = (paras[0] if paras else "")[:110] or item["title"]
        if len(summary) < 40 and tables:
            summary = item["title"] + "：共收录 " + str(len(tables[0]) - 1) + " 条，含常用方法与用法说明。"

        write_entry(root, item, paras, table_md, summary)
        ok += 1
        print("OK", item["slug"], len(summary), "字摘要", ("+表%d行" % len(tables[0])) if table_md else "无表")
        time.sleep(reg["rules"].get("fetch_interval_sec", 0.4))

    print("完成:", ok, "/", len(targets))


if __name__ == "__main__":
    main()
