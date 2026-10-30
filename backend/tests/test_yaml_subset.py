"""`yaml_subset`（模板 YAML 最小子集）的单元测试。

覆盖 5 种模板结构：标量类型、嵌套映射、映射序列、序列内嵌套序列、
真实模板文件；3 种非法输入：制表符缩进、流式集合、引号未闭合/块标量。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import yaml_subset


def test_scalar_types_and_comments():
    text = """
# 整行注释
title: 前端入门 8 周
weeks: 8
ratio: 0.75
enabled: true
disabled: False
empty: null
tilde: ~
quoted: "含 # 与 : 的字符串"
single: 'it''s ok'
inline: 42  # 行尾注释
"""
    data = yaml_subset.loads(text)
    assert data["title"] == "前端入门 8 周"
    assert data["weeks"] == 8 and isinstance(data["weeks"], int)
    assert data["ratio"] == 0.75
    assert data["enabled"] is True and data["disabled"] is False
    assert data["empty"] is None and data["tilde"] is None
    assert data["quoted"] == "含 # 与 : 的字符串"
    assert data["single"] == "it's ok"
    assert data["inline"] == 42


def test_nested_mapping():
    text = """
label:
  zh: 前端入门
  en: Frontend Entry
meta:
  nested:
    deep: 1
"""
    data = yaml_subset.loads(text)
    assert data["label"] == {"zh": "前端入门", "en": "Frontend Entry"}
    assert data["meta"]["nested"]["deep"] == 1


def test_sequence_of_mappings():
    text = """
stages:
  - title: 第一阶段
    weeks: 2
  - title: 第二阶段
    weeks: 1
"""
    data = yaml_subset.loads(text)
    assert data["stages"] == [
        {"title": "第一阶段", "weeks": 2},
        {"title": "第二阶段", "weeks": 1},
    ]


def test_mapping_with_nested_sequence():
    text = """
stages:
  - title: HTML
    items:
      - title: 盒模型
        est_minutes: 20
      - title: 静态页
        est_minutes: 90
    tags:
      - css
      - layout
"""
    data = yaml_subset.loads(text)
    stage = data["stages"][0]
    assert stage["items"][1]["title"] == "静态页"
    assert stage["tags"] == ["css", "layout"]


def test_real_templates_parse():
    paths = sorted(Path(__file__).resolve().parents[2]
                   .joinpath("content", "_meta", "templates").glob("*.yml"))
    assert len(paths) == 4
    for path in paths:
        data = yaml_subset.load(path)
        assert data["stages"] and len(data["stages"]) >= 4
        for stage in data["stages"]:
            assert stage["items"]
            for item in stage["items"]:
                assert "title" in item


def test_tab_indent_is_rejected():
    with pytest.raises(yaml_subset.YamlSubsetError) as exc:
        yaml_subset.loads("title: x\n\tweeks: 8\n")
    assert exc.value.lineno == 2
    assert "制表符" in str(exc.value)


def test_flow_collection_is_rejected():
    with pytest.raises(yaml_subset.YamlSubsetError) as exc:
        yaml_subset.loads("stages: [{title: a}]\n")
    assert exc.value.lineno == 1
    assert "流式" in str(exc.value)


def test_unclosed_quote_reports_line():
    with pytest.raises(yaml_subset.YamlSubsetError) as exc:
        yaml_subset.loads("title: ok\ngoal: \"没有闭合\n")
    assert exc.value.lineno == 2
    assert "未闭合" in str(exc.value)


def test_block_scalar_is_rejected():
    with pytest.raises(yaml_subset.YamlSubsetError) as exc:
        yaml_subset.loads("summary: |\n  line1\n  line2\n")
    assert exc.value.lineno == 1
    assert "块标量" in str(exc.value)
