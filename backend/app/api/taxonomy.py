"""分类树（需求文档 §4.6；`API.md` §3.2）。

两个来源：
- 计数来自库里实际的 `stack/category` 聚合（`is_deleted = 0`），保证不出现空分类；
- `stacks[].label` 来自 `content/_meta/taxonomy.yml`（API.md §6 第 7 项，M2 补）。

`label` 缺失或文件坏掉时**降级为 `null`**，接口照常 200：前端在拿不到 label 时退回显示
`stack` 原始名（`MODULES.md` §6.2），不该因为一份可选配置文件让整棵分类树 5xx。

**为什么不用 `services/yaml_subset.py`**：`taxonomy.yml` 的 label 写成流式映射
`{ zh: 计算机基础, en: CS Basics }`，而那个子集解析器明确不支持流式集合（`services/yaml_subset.py`
文件头的“不支持”清单）。改 `content/_meta/` 是前端骨架的文件，本模块不越界，所以这里用一个
只认「`stacks:` → `label:` → `zh/en`」的小读取器，同时兼容块式与流式两种写法。
"""
from pathlib import Path
import re

from fastapi import APIRouter, Depends

from ..api.deps import get_conn
from ..core.config import SETTINGS

router = APIRouter()

#: taxonomy.yml 里 `label` 允许的两个语言键，与 `models.LocalizedLabel` 一致。
LABEL_LANGS = ("zh", "en")

_STACK_KEY = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
_LABEL_LINE = re.compile(r"^    label:\s*(.*)$")
_FLOW_PAIR = re.compile(r"([A-Za-z]{2})\s*:\s*([^,}]+)")
_BLOCK_PAIR = re.compile(r"^      ([A-Za-z]{2}):\s*(.+?)\s*$")


def taxonomy_file() -> Path:
    """`content/_meta/taxonomy.yml`；测试可替换本函数。"""
    return Path(SETTINGS.content_dir) / "_meta" / "taxonomy.yml"


def parse_labels(text: str) -> dict[str, dict]:
    """从 taxonomy.yml 文本里抽 `{stack: {zh, en}}`，只认 `stacks.<stack>.label`。"""
    labels: dict[str, dict] = {}
    stack: str | None = None
    in_stacks = False
    pending: dict | None = None          # 块式写法：label: 之后逐行收集 zh/en

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].rstrip() if raw.lstrip().startswith("#") else raw.rstrip()
        if not line.strip():
            continue

        if not line.startswith(" "):     # 顶层键
            in_stacks = line.strip() == "stacks:"
            stack = None
            pending = None
            continue
        if not in_stacks:
            continue

        if pending is not None:
            pair = _BLOCK_PAIR.match(line)
            if pair and pair.group(1) in LABEL_LANGS:
                pending[pair.group(1)] = pair.group(2).strip().strip("'\"")[:40]
                if len(pending) == len(LABEL_LANGS):
                    labels[stack] = pending
                    pending = None
                continue
            if line.startswith("      "):      # label 下还有别的键：不认，放弃这条
                continue
            pending = None

        match_stack = _STACK_KEY.match(line)
        if match_stack:
            stack = match_stack.group(1)
            continue

        match_label = _LABEL_LINE.match(line)
        if not match_label or not stack:
            continue
        value = match_label.group(1).strip()
        if value.startswith("{") and value.endswith("}"):
            flow = {}
            for lang, label in _FLOW_PAIR.findall(value):
                if lang in LABEL_LANGS:
                    flow[lang] = label.strip().strip("'\"")[:40]
            if len(flow) == len(LABEL_LANGS) and all(flow.values()):
                labels[stack] = flow
        elif not value:                    # 块式写法的入口
            pending = {}
    return labels


def load_labels() -> dict[str, dict]:
    """读标签表；文件不存在 / 读失败都只是少一份 label，不抛异常。"""
    path = taxonomy_file()
    try:
        return parse_labels(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


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

    labels = load_labels()
    stacks = {}
    for r in rows:
        node = stacks.setdefault(r["stack"], {"stack": r["stack"], "total": 0, "categories": []})
        node["categories"].append({"category": r["category"], "count": r["n"]})
        node["total"] += r["n"]

    levels = [r["level"] for r in conn.execute(
        "SELECT level, COUNT(*) n FROM resources WHERE is_deleted = 0 GROUP BY level ORDER BY n DESC")]
    types = [r["type"] for r in conn.execute(
        "SELECT type, COUNT(*) n FROM resources WHERE is_deleted = 0 GROUP BY type ORDER BY n DESC")]

    result = sorted(stacks.values(), key=lambda x: -x["total"])
    for node in result:
        node["label"] = labels.get(node["stack"])

    return {
        "stacks": result,
        # D25 25.5：按数量降序（`ORDER BY n DESC` 已经查好了，别再 `sorted(set())` 打回字典序）
        "levels": _dedupe_keep_order(levels),
        "types": _dedupe_keep_order(types),
        "total": sum(s["total"] for s in stacks.values()),
    }
