"""front-matter 解析（需求文档 §4.2）。

只支持本项目的 YAML 子集：标量、带引号字符串、行内数组。
不引入 PyYAML，避免为一个 15 行格式加一个依赖。
"""
import json
import re

FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
        body = v[1:-1]
        if v[0] == "\"":
            body = body.replace('\\"', '"').replace("\\\\", "\\")
        return body
    return v


def _parse_list(v):
    inner = v.strip()[1:-1].strip()
    if not inner:
        return []
    parts, buf, quote = [], "", None
    for ch in inner:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf += ch
        elif ch in ("\"", "'"):
            quote = ch
        elif ch == ",":
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def parse(text):
    """返回 (meta: dict, body: str)。没有 front-matter 时 meta 为空。"""
    m = FM.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip(), val.strip()
        # 去掉行尾注释（只处理「  # 」这种带空格的情况，避免误伤 URL 里的 #）
        if "  # " in val:
            val = val.split("  # ", 1)[0].rstrip()
        if val.startswith("[") and val.endswith("]"):
            meta[key] = _parse_list(val)
        elif val.startswith('"') or val.startswith("'"):
            meta[key] = _unquote(val)
        else:
            meta[key] = val
    return meta, text[m.end():]


def slug_from_path(rel_path):
    p = rel_path.replace("\\", "/")
    if p.startswith("content/"):
        p = p[len("content/"):]
    if p.endswith(".md"):
        p = p[:-3]
    return p


def dump_tags(tags):
    return json.dumps(tags, ensure_ascii=False)
