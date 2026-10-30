#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
optimize_media.py — 把 content/ 里超过 300KB 的图片转成 WebP，并改写正文引用

规则（§4.3）：单文件 ≤300KB，长边 ≤1600px。
用法: python tools/optimize_media.py --content ./content
"""
import argparse
import re
from pathlib import Path

from PIL import Image

LIMIT = 300 * 1024
MAX_EDGE = 1600


def convert(src: Path, dst: Path) -> int:
    img = Image.open(src)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, MAX_EDGE / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    quality = 82
    while quality >= 40:
        img.save(dst, "WEBP", quality=quality, method=6)
        if dst.stat().st_size <= LIMIT:
            break
        quality -= 8
    return dst.stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", default="./content")
    args = ap.parse_args()
    root = Path(args.content)

    images = [p for p in root.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    targets = [p for p in images if p.stat().st_size > LIMIT]
    if not targets:
        print("没有超过 300KB 的图片")
        return

    for p in targets:
        dst = p.with_suffix(".webp")
        before = p.stat().st_size
        after = convert(p, dst)
        rel_old = p.name
        rel_new = dst.name
        touched = 0
        for md in root.rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            if rel_old in text:
                md.write_text(text.replace(rel_old, rel_new), encoding="utf-8", newline="\n")
                touched += 1
        p.unlink()
        print("{}: {:.0f}KB -> {:.0f}KB ({})，改写 {} 个文件".format(
            p.name, before / 1024, after / 1024, dst.name, touched))


if __name__ == "__main__":
    main()
