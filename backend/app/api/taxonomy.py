"""分类树（需求文档 §4.6；`API.md` §3.2）。

两个来源：
- 计数来自库里实际的 `stack/category` 聚合（`is_deleted = 0`），保证不出现空分类；
- `stacks[].label` 与 `stacks[].categories[].label` 来自 `content/_meta/taxonomy.yml`
  （API.md §6 第 7 项、D28，M2 补）。

`label` 缺失、只写了一个语言键、分类未登记或文件坏掉时**降级为 `null`**，接口照常 200：
前端在拿不到 label 时退回显示 `stack` / `category` 原始名（`MODULES.md` §6.2、D28），
不该因为一份可选配置文件让整棵分类树 5xx。

**为什么不用 `services/yaml_subset.py`**：`taxonomy.yml` 的 label 写成流式映射
`{ zh: 计算机基础, en: CS Basics }`，而那个子集解析器明确不支持流式集合（`services/yaml_subset.py`
文件头的“不支持”清单）。改 `content/_meta/` 是前端骨架的文件，本模块不越界，所以这里用一个
只认「`stacks:` → `label:` / `category_labels:`」的小读取器，同时兼容块式与流式两种写法。
"""
from dataclasses import dataclass, field
from pathlib import Path
import re

from fastapi import APIRouter, Depends

from ..api.deps import get_conn
from ..core.config import SETTINGS

router = APIRouter()

#: taxonomy.yml 里 label 允许的两个语言键，与 `models.LocalizedLabel` 一致。
LABEL_LANGS = ("zh", "en")

_STACK_KEY = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
_STACK_LABEL = re.compile(r"^    label:\s*(.*)$")
_CATEGORY_LABELS_KEY = re.compile(r"^    category_labels:\s*$")
_CATEGORY_LABEL = re.compile(r"^      ([A-Za-z0-9_.-]+):\s*(.*)$")
_FLOW_PAIR = re.compile(r"([A-Za-z]{2})\s*:\s*([^,}]+)")
_BLOCK_PAIR = re.compile(r"^ +([A-Za-z]{2}):\s*(.+?)\s*$")


@dataclass(frozen=True)
class TaxonomyLabels:
    """`taxonomy.yml` 里的两套本地化名（只收录两个语言键齐全的条目）。"""

    #: `{stack: {zh, en}}`
    stacks: dict[str, dict] = field(default_factory=dict)
    #: `{(stack, category): {zh, en}}`
    categories: dict[tuple[str, str], dict] = field(default_factory=dict)


def taxonomy_file() -> Path:
    """`content/_meta/taxonomy.yml`；测试可替换本函数。"""
    return Path(SETTINGS.content_dir) / "_meta" / "taxonomy.yml"


def _clean(value: str) -> str:
    """去掉引号/首尾空白并限长：与既有 `stacks[].label` 的取值口径一致。"""
    return value.strip().strip("'\"").strip()[:40]


def _flow_label(value: str) -> dict | None:
    """解析 `{ zh: …, en: … }`；不是流式映射或不是两个语言键都齐 → None。"""
    if not (value.startswith("{") and value.endswith("}")):
        return None
    found: dict[str, str] = {}
    for lang, label in _FLOW_PAIR.findall(value):
        if lang in LABEL_LANGS:
            cleaned = _clean(label)
            if cleaned:
                found[lang] = cleaned
    if all(found.get(lang) for lang in LABEL_LANGS):
        return found
    return None


def parse_taxonomy_labels(text: str) -> TaxonomyLabels:
    """从 taxonomy.yml 文本里抽 stack / category 的本地化名。

    只认 `stacks.<stack>.label` 与 `stacks.<stack>.category_labels.<category>`；
    流式（`{ zh: …, en: … }`）与块式缩进两种写法都支持。**两个语言键都齐**才收录，
    缺任一或值为空视为未登记——调用方据此把 `label` 降级为 `None`。
    """
    stack_labels: dict[str, dict] = {}
    category_labels: dict[tuple[str, str], dict] = {}
    in_stacks = False
    in_category_labels = False
    stack: str | None = None

    # 块式写法的收集状态：block_stack 非 None 表示正在等 zh/en 两行。
    block_stack: str | None = None
    block_category: str | None = None
    block_indent = 0
    block_values: dict[str, str] = {}

    def flush_block() -> None:
        """把收集满两个语言键的块落库；不满则整块丢弃。"""
        nonlocal block_stack, block_category, block_values
        if (
            block_stack is not None
            and len(block_values) == len(LABEL_LANGS)
            and all(block_values.get(lang) for lang in LABEL_LANGS)
        ):
            if block_category is None:
                stack_labels[block_stack] = dict(block_values)
            else:
                category_labels[(block_stack, block_category)] = dict(block_values)
        block_stack = None
        block_category = None
        block_values = {}

    def start_block(indent: int, category: str | None) -> None:
        nonlocal block_stack, block_category, block_indent, block_values
        flush_block()
        if stack is None:
            return
        block_stack = stack
        block_category = category
        block_indent = indent
        block_values = {}

    for raw in (text or "").splitlines():
        if raw.lstrip().startswith("#"):
            continue                      # 整行注释：块内注释不打断收集
        line = raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))

        # ---- 块式收集：先消费，缩进回退时结束并继续按普通行处理。
        if block_stack is not None:
            if indent > block_indent:
                continue
            if indent == block_indent:
                pair = _BLOCK_PAIR.match(line)
                if pair and pair.group(1) in LABEL_LANGS:
                    value = _clean(pair.group(2))
                    if value:
                        block_values[pair.group(1)] = value
                    continue
            flush_block()

        if indent == 0:
            in_stacks = line.strip() == "stacks:"
            in_category_labels = False
            stack = None
            continue
        if not in_stacks:
            continue

        if indent == 2:
            match_stack = _STACK_KEY.match(line)
            if match_stack:
                stack = match_stack.group(1)
                in_category_labels = False
            continue

        if indent == 4:
            if stack is None:
                continue
            in_category_labels = False
            match_label = _STACK_LABEL.match(line)
            if match_label:
                value = match_label.group(1).strip()
                if value:
                    flow = _flow_label(value)
                    if flow is not None:
                        stack_labels[stack] = flow
                else:
                    start_block(6, None)
                continue
            if _CATEGORY_LABELS_KEY.match(line):
                in_category_labels = True
            continue

        if indent == 6:
            if not stack or not in_category_labels:
                continue
            match_category = _CATEGORY_LABEL.match(line)
            if not match_category:
                continue
            category, value = match_category.group(1), match_category.group(2).strip()
            if value:
                flow = _flow_label(value)
                if flow is not None:
                    category_labels[(stack, category)] = flow
            else:
                start_block(8, category)
            continue

        # 更深的缩进只可能是块式内容（上面已处理）或 categories 列表续行，忽略。

    flush_block()
    return TaxonomyLabels(stacks=stack_labels, categories=category_labels)


def parse_labels(text: str) -> dict[str, dict]:
    """兼容既有调用：只取 `stacks[].label`。"""
    return dict(parse_taxonomy_labels(text).stacks)


def load_taxonomy_labels() -> TaxonomyLabels:
    """读整份标签表；文件不存在 / 编码坏掉 / 解析异常都只降级为空表，不抛异常。

    这是可选配置：任何失败都不该让 `GET /api/taxonomy` 5xx（API.md §3.2、D28）。
    """
    try:
        return parse_taxonomy_labels(taxonomy_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 可选内容文件，读/解码/解析失败一律降级
        return TaxonomyLabels()


def load_labels() -> dict[str, dict]:
    """只要 stack 标签（既有函数，行为不变）。"""
    return dict(load_taxonomy_labels().stacks)


def load_category_labels() -> dict[tuple[str, str], dict]:
    """只要分类标签：`{(stack, category): {zh, en}}`。"""
    return dict(load_taxonomy_labels().categories)


def _dedupe_keep_order(values):
    """去重但**保持传入顺序**（D25 25.5：`levels` / `types` 按数量降序，不能打回字典序）。"""
    seen = set()
    ordered = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


@router.get("/taxonomy")
def api_taxonomy(conn=Depends(get_conn)):
    rows = conn.execute("""
        SELECT stack, category, COUNT(*) AS n
        FROM resources
        WHERE is_deleted = 0
        GROUP BY stack, category
        ORDER BY stack, category
    """).fetchall()

    labels = load_taxonomy_labels()
    stacks = {}
    for r in rows:
        node = stacks.setdefault(r["stack"], {"stack": r["stack"], "total": 0, "categories": []})
        node["categories"].append({
            "category": r["category"],
            "count": r["n"],
            # D28：只透传完整登记 {zh, en} 的分类；未登记 / 缺语言键 → None
            "label": labels.categories.get((r["stack"], r["category"])),
        })
        node["total"] += r["n"]

    levels = [r["level"] for r in conn.execute(
        "SELECT level, COUNT(*) n FROM resources WHERE is_deleted = 0 GROUP BY level ORDER BY n DESC")]
    types = [r["type"] for r in conn.execute(
        "SELECT type, COUNT(*) n FROM resources WHERE is_deleted = 0 GROUP BY type ORDER BY n DESC")]

    result = sorted(stacks.values(), key=lambda x: -x["total"])
    for node in result:
        node["label"] = labels.stacks.get(node["stack"])

    return {
        "stacks": result,
        # D25 25.5：按数量降序（`ORDER BY n DESC` 已经查好了，别再 `sorted(set())` 打回字典序）
        "levels": _dedupe_keep_order(levels),
        "types": _dedupe_keep_order(types),
        "total": sum(s["total"] for s in stacks.values()),
    }
