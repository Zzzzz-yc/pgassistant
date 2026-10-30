"""提示词（M4）。提示词与代码分文件，便于版本比对与 review。

三份（契约 `MODULES.md` §1.1 `backend/app/prompts/**`）：

- `classify.md`：L2 意图分类（§7.5 的严格 JSON 契约）
- `plan.md`：两段式的第二段（候选集注入、只许用候选 slug、§7.6）
- `advice.md`：学习建议（必须引用站内资料 + 免责声明，§7.3）

占位符用 `<<NAME>>` 而不是 `str.format` 的花括号：提示词里全是 JSON 示例，
花括号会被 `format` 吃掉。渲染用 `render(text, NAME=...)`。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent

#: `<<NAME>>` → 值。只替换 `\w+`，避免误伤提示词里的普通文本。
PLACEHOLDER = re.compile(r"<<([A-Z_][A-Z0-9_]*)>>")


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """读 `prompts/<name>.md`（缓存：进程内文件不会变）。"""
    filename = name if name.endswith(".md") else name + ".md"
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def render(text: str, **values: object) -> str:
    """替换 `<<NAME>>` 占位符；未提供的占位符原样保留（便于发现漏传）。"""

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        return str(values[key])

    return PLACEHOLDER.sub(replace, text)
