#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_content.py — 内容包校验（需求文档 §4.2 / §4.5 / §5.1）。

错误（退出码 1）会让 CI 失败；警告只提示。
用法: python backend/scripts/validate_content.py --content ./content
"""
import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.services import frontmatter            # noqa: E402

REQUIRED = ["slug", "title", "summary", "stack", "category", "level", "type",
            "author", "license"]
LEVELS = {"beginner", "intermediate", "advanced"}
TYPES = {"article", "video", "course", "book", "docs", "exercise", "summary", "overview"}
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4"}
SINGLE_LIMIT = 300 * 1024
TOTAL_LIMIT = 50 * 1024 * 1024
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", default=str(PROJECT / "content"))
    args = ap.parse_args()
    root = Path(args.content)

    if not root.is_dir():
        raise SystemExit("内容目录不存在: " + str(root))

    errors, warns = [], []
    slugs, titles = {}, {}
    files = sorted(p for p in root.rglob("*") if p.is_file())
    mds = [p for p in files if p.suffix == ".md" and not p.name.startswith("CREDITS")]

    total_media = 0
    for p in files:
        if p.suffix.lower() in MEDIA_EXT:
            size = p.stat().st_size
            total_media += size
            if size > SINGLE_LIMIT:
                errors.append("媒体过大 %.0fKB > 300KB: %s" % (size / 1024, p.relative_to(root)))

    if total_media > TOTAL_LIMIT:
        errors.append("媒体总量 %.1fMB > 50MB" % (total_media / 1024 / 1024))

    for md in mds:
        rel = md.relative_to(root).as_posix()
        meta, body = frontmatter.parse(md.read_text(encoding="utf-8"))

        for key in REQUIRED:
            if not meta.get(key):
                errors.append("缺少字段 %s: %s" % (key, rel))

        if meta.get("level") and meta["level"] not in LEVELS:
            errors.append("level 非法 (%s): %s" % (meta["level"], rel))
        if meta.get("type") and meta["type"] not in TYPES:
            errors.append("type 非法 (%s): %s" % (meta["type"], rel))

        # slug 必须与路径一致（§5.1）
        expect = frontmatter.slug_from_path("content/" + rel)
        if meta.get("slug") and meta["slug"] != expect:
            errors.append("slug 与路径不一致: %s != %s" % (meta["slug"], expect))

        # category 必须等于所在目录名
        parts = rel.split("/")
        if len(parts) >= 3 and meta.get("category") and meta["category"] != parts[1]:
            errors.append("category 与目录不一致 (%s vs %s): %s" % (meta["category"], parts[1], rel))

        # 本地链接必须存在
        for target in LINK.findall(body):
            if target.startswith(("http", "#", "mailto")):
                continue
            if not (md.parent / target).exists():
                errors.append("图片/链接不存在: %s -> %s" % (rel, target))

        slug = meta.get("slug", "")
        if slug:
            slugs.setdefault(slug, []).append(rel)
        title = meta.get("title", "")
        if title:
            titles.setdefault(title, []).append(rel)

        summary = str(meta.get("summary", ""))
        if len(summary) < 25:
            warns.append("摘要过短 (%d 字): %s" % (len(summary), rel))
        if len(body.strip()) < 300 and meta.get("type") not in ("overview",):
            warns.append("正文过短 (%d 字符): %s" % (len(body.strip()), rel))
        if meta.get("license") == "proprietary" and not meta.get("source_url"):
            errors.append("非开放许可必须填 source_url: " + rel)

    for slug, where in slugs.items():
        if len(where) > 1:
            errors.append("重复 slug %s: %s" % (slug, ", ".join(where)))
    for title, where in titles.items():
        if len(where) > 1:
            warns.append("重复标题 %s: %s" % (title, ", ".join(where)))

    print("文件 %d（md %d），媒体 %.1f MB" % (len(files), len(mds), total_media / 1024 / 1024))
    print("错误 %d，警告 %d" % (len(errors), len(warns)))

    for e in errors[:40]:
        print("  [E] " + e)
    if len(errors) > 40:
        print("  ... 其余 %d 条错误省略" % (len(errors) - 40))
    for w in warns[:15]:
        print("  [W] " + w)
    if len(warns) > 15:
        print("  ... 其余 %d 条警告省略" % (len(warns) - 15))

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
