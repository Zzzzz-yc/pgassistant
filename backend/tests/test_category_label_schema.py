"""`CategoryNode.label` 的契约用例（API.md §3.2 / DECISIONS D28）。

只验契约层三件事，不依赖数据库、不碰 `api/taxonomy.py` 的读取实现：

1. 不带 label 时缺省为 `None`（M1 旧响应与未登记分类都能解析）；
2. 带 label 时按 `LocalizedLabel` 序列化，且嵌在 `StackNode` / `TaxonomyResponse` 里仍然成立；
3. 加法式：既有 `category` / `count` 字段语义不变。
"""
from __future__ import annotations

from app.schemas.models import CategoryNode, LocalizedLabel, StackNode, TaxonomyResponse


def test_category_label_defaults_to_none():
    node = CategoryNode(category="array-linkedlist", count=7)

    assert node.label is None
    assert node.model_dump() == {"category": "array-linkedlist", "count": 7, "label": None}


def test_category_label_serializes_when_present():
    node = CategoryNode(
        category="interview",
        count=15,
        label={"zh": "面试题", "en": "Interview"},
    )

    assert node.label == LocalizedLabel(zh="面试题", en="Interview")
    assert node.model_dump() == {
        "category": "interview",
        "count": 15,
        "label": {"zh": "面试题", "en": "Interview"},
    }


def test_old_response_without_label_still_parses():
    """M1 响应没有 `label` 键：必须按 None 解析，不得 422。"""
    node = CategoryNode.model_validate({"category": "sorting", "count": 13})

    assert node.label is None


def test_taxonomy_payload_keeps_mixed_label_states():
    payload = {
        "stacks": [
            {
                "stack": "cs-basics",
                "total": 28,
                "categories": [
                    {"category": "sorting", "count": 13, "label": None},
                    {
                        "category": "interview",
                        "count": 15,
                        "label": {"zh": "面试题", "en": "Interview"},
                    },
                ],
                "label": {"zh": "计算机基础", "en": "CS Basics"},
            }
        ],
        "levels": ["beginner"],
        "types": ["article"],
        "total": 28,
    }

    parsed = TaxonomyResponse.model_validate(payload)

    categories = parsed.stacks[0].categories
    assert [c.category for c in categories] == ["sorting", "interview"]
    assert categories[0].label is None
    assert categories[1].label is not None
    assert categories[1].label.en == "Interview"
    assert parsed.model_dump()["stacks"][0]["categories"][0]["label"] is None


def test_stack_categories_without_label_field_are_backward_compatible():
    stack = StackNode.model_validate(
        {
            "stack": "frontend",
            "total": 2,
            "categories": [{"category": "html-css", "count": 2}],
        }
    )

    assert stack.categories[0].label is None
