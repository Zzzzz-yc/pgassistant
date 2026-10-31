"""`CategoryNode.label` 数据侧用例（`API.md` §3.2 / DECISIONS D28）。

只验 `content/_meta/taxonomy.yml` 的登记与 `api/taxonomy.py` 的读取/透传：

1. 全部登记 → 每个分类都返回 `{zh, en}`，且与 `categories:` 列表逐项对齐（不漏、不多）；
2. 某分类未登记 / 只写了一个语言键 → 该条 `label` 为 `null`，其它条正常；
3. `taxonomy.yml` 缺失 / 损坏 → 接口仍 200，且 `label` 全 `null`；
4. 既有 `stacks[].label` 行为不变。

`conftest.py` 是冻结文件，这里自带夹具；不修改任何既有测试。
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from app.api import taxonomy as taxonomy_api
from app.db.connection import connect

REPO = Path(__file__).resolve().parents[2]
TAXONOMY = REPO / "content" / "_meta" / "taxonomy.yml"

_STACK = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
_CATEGORIES = re.compile(r"^    categories:\s*\[(.*)$")


def _registry(text: str) -> dict[str, list[str]]:
    """从 taxonomy.yml 抽出 `{stack: [category, ...]}`（只用于本测试的对齐断言）。"""
    result: dict[str, list[str]] = {}
    stack: str | None = None
    lines = (text or "").splitlines()
    index = 0
    while index < len(lines):
        if lines[index].lstrip().startswith("#"):
            index += 1
            continue
        line = lines[index].rstrip()
        match_stack = _STACK.match(line)
        if match_stack:
            stack = match_stack.group(1)
            result.setdefault(stack, [])
            index += 1
            continue
        match_categories = _CATEGORIES.match(line)
        if match_categories and stack:
            body = match_categories.group(1)
            while "]" not in body and index + 1 < len(lines):
                index += 1
                body += " " + lines[index].strip()
            body = body.split("]", 1)[0]
            result[stack] = [item.strip() for item in body.split(",") if item.strip()]
        index += 1
    return result


# ------------------------------------------------------------ 解析器（无库）

def test_every_registered_category_has_full_localized_label():
    """taxonomy.yml 的每个 category 都登记了完整 `{zh, en}`，且没有多余条目。"""
    text = TAXONOMY.read_text(encoding="utf-8")
    registry = _registry(text)
    labels = taxonomy_api.parse_taxonomy_labels(text)

    expected = {(stack, category) for stack, items in registry.items() for category in items}
    assert len(expected) == 27                           # 4 个 stack 合计 27 个分类
    assert set(labels.categories) == expected            # 一个不漏、一个不多
    for stack, category in expected:
        label = labels.categories[(stack, category)]
        assert set(label) == {"zh", "en"}
        assert label["zh"].strip() and label["en"].strip()


def test_category_labels_support_flow_and_block_style(tmp_path, monkeypatch):
    """流式与块式都认；缺一个语言键的条目不收录。"""
    meta = tmp_path / "_meta"
    meta.mkdir()
    path = meta / "taxonomy.yml"
    path.write_text(
        "stacks:\n"
        "  cs-basics:\n"
        "    label: { zh: 计算机基础, en: CS Basics }\n"
        "    categories: [sorting, graph, heap]\n"
        "    category_labels:\n"
        "      sorting: { zh: 排序, en: Sorting }\n"
        "      graph:\n"
        "        zh: 图\n"
        "        en: Graphs\n"
        "      heap: { zh: 堆 }\n"
        "  frontend:\n"
        "    label:\n"
        "      zh: 前端\n"
        "      en: Frontend\n"
        "    categories: [html-css]\n"
        "    category_labels:\n"
        "      html-css: { zh: HTML 与 CSS, en: HTML & CSS }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: path)

    labels = taxonomy_api.load_taxonomy_labels()
    assert labels.categories == {
        ("cs-basics", "sorting"): {"zh": "排序", "en": "Sorting"},
        ("cs-basics", "graph"): {"zh": "图", "en": "Graphs"},
        ("frontend", "html-css"): {"zh": "HTML 与 CSS", "en": "HTML & CSS"},
    }
    # 既有 stacks[].label 两种写法都保持原行为（含块式）
    assert labels.stacks == {
        "cs-basics": {"zh": "计算机基础", "en": "CS Basics"},
        "frontend": {"zh": "前端", "en": "Frontend"},
    }
    assert taxonomy_api.load_labels() == labels.stacks


def test_load_degrades_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: tmp_path / "nope.yml")
    assert taxonomy_api.load_taxonomy_labels().categories == {}
    assert taxonomy_api.load_labels() == {}


def test_load_degrades_when_file_is_broken(tmp_path, monkeypatch):
    """非 UTF-8 的坏文件：读取抛异常也必须降级为空表，不向外抛。"""
    path = tmp_path / "taxonomy.yml"
    path.write_bytes(b"\xff\xfe\x00stacks:\n  broken: [")
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: path)
    assert taxonomy_api.load_category_labels() == {}
    assert taxonomy_api.load_labels() == {}


# ------------------------------------------------------- 接口透传（带库）

def _insert_resource(db_path: Path, slug: str, stack: str, category: str, title: str) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO resources
                   (slug, title, summary, body_md, body_tokens, stack, category, level,
                    type, duration_min, file_path, content_hash, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (slug, title, title, "正文", "正 文", stack, category, "beginner",
                 "article", 5, "content/x.md", "hash-" + slug, "2026-10-30", "2026-10-30"),
            )
            row = conn.execute("SELECT id FROM resources WHERE slug = ?", (slug,)).fetchone()
            conn.execute(
                "INSERT INTO resources_fts (rowid, title_tokens, summary_tokens,"
                " body_tokens, tags_tokens) VALUES (?,?,?,?,?)",
                (row["id"], title, title, title, ""),
            )
    finally:
        conn.close()


@pytest.fixture()
def auth(token) -> dict[str, str]:
    return {"X-App-Token": token}


@pytest.fixture()
def all_categories(db_path):
    """把 taxonomy.yml 里登记的 27 个分类都塞进库（conftest 已有一条 array-linkedlist）。"""
    text = TAXONOMY.read_text(encoding="utf-8")
    for stack, categories in _registry(text).items():
        for category in categories:
            slug = f"{stack}/{category}/label-test"
            conn = connect(db_path)
            try:
                exists = conn.execute(
                    "SELECT 1 FROM resources WHERE slug = ?", (slug,)
                ).fetchone()
            finally:
                conn.close()
            if not exists:
                _insert_resource(db_path, slug, stack, category, f"{category} 测试")
    return db_path


@pytest.fixture()
def partial_categories(db_path):
    """在 conftest 的 array-linkedlist 之外，再补 sorting / graph 两条。"""
    _insert_resource(db_path, "cs-basics/sorting/label-test", "cs-basics", "sorting", "排序")
    _insert_resource(db_path, "cs-basics/graph/label-test", "cs-basics", "graph", "图")
    return db_path


def test_endpoint_returns_label_for_every_registered_category(client, auth, all_categories):
    response = client.get("/api/taxonomy", headers=auth)
    assert response.status_code == 200

    registry = _registry(TAXONOMY.read_text(encoding="utf-8"))
    expected = taxonomy_api.load_category_labels()
    expected_stacks = taxonomy_api.load_labels()
    seen: set[tuple[str, str]] = set()
    for stack in response.json()["stacks"]:
        assert stack["label"] == expected_stacks.get(stack["stack"])
        assert stack["label"] is not None
        for category in stack["categories"]:
            key = (stack["stack"], category["category"])
            assert key in expected
            assert category["label"] == expected[key]
            assert set(category["label"]) == {"zh", "en"}
            seen.add(key)

    assert seen == {(stack, category) for stack, items in registry.items() for category in items}


def test_unregistered_and_one_language_labels_are_null_others_intact(
    tmp_path, monkeypatch, client, auth, partial_categories
):
    path = tmp_path / "taxonomy.yml"
    path.write_text(
        "stacks:\n"
        "  cs-basics:\n"
        "    label: { zh: 计算机基础, en: CS Basics }\n"
        "    categories: [array-linkedlist, sorting, graph]\n"
        "    category_labels:\n"
        "      array-linkedlist: { zh: 数组与链表, en: Arrays & Linked Lists }\n"
        "      sorting: { zh: 排序 }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: path)

    response = client.get("/api/taxonomy", headers=auth)
    assert response.status_code == 200
    stack = response.json()["stacks"][0]
    by_category = {item["category"]: item for item in stack["categories"]}

    # 完整登记 → 透传
    assert by_category["array-linkedlist"]["label"] == {
        "zh": "数组与链表", "en": "Arrays & Linked Lists",
    }
    # 只写了一个语言键 → null（不是空串、不是中文单语）
    assert by_category["sorting"]["label"] is None
    # 完全未登记 → null
    assert by_category["graph"]["label"] is None
    # 既有 stacks[].label 不受影响
    assert stack["label"] == {"zh": "计算机基础", "en": "CS Basics"}


def test_missing_taxonomy_file_keeps_200_with_null_labels(
    tmp_path, monkeypatch, client, auth, partial_categories
):
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: tmp_path / "nope.yml")

    response = client.get("/api/taxonomy", headers=auth)
    assert response.status_code == 200
    stacks = response.json()["stacks"]
    assert stacks                                   # 分类树本身仍返回
    for stack in stacks:
        assert stack["label"] is None
        assert stack["categories"]
        for category in stack["categories"]:
            assert category["label"] is None


def test_broken_taxonomy_file_keeps_200_with_null_labels(
    tmp_path, monkeypatch, client, auth, partial_categories
):
    path = tmp_path / "taxonomy.yml"
    path.write_bytes(b"\xff\xfe\x00stacks:\n  cs-basics: [")
    monkeypatch.setattr(taxonomy_api, "taxonomy_file", lambda: path)

    response = client.get("/api/taxonomy", headers=auth)
    assert response.status_code == 200
    for stack in response.json()["stacks"]:
        assert stack["label"] is None
        for category in stack["categories"]:
            assert category["label"] is None
