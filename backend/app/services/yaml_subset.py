"""最小 YAML 子集解析器（模板专用，**不引入 PyYAML**）。

需求文档 §8.6 的模板是 `content/_meta/templates/*.yml`，结构固定为 §7.6 的
`plan_draft`（块映射 + 块序列 + 标量）。为一个只读的模板目录引入 PyYAML
违背「不新增重依赖」（RULES §7），所以这里实现够用的子集，并在遇到不支持
的语法时给出**带行号**的错误，而不是静默解析成错值。

支持：
- 注释（整行 `#` 与行尾 ` #`，引号内的 `#` 不算注释）
- 块映射 `key: value`（嵌套）
- 块序列 `- value` 与 `- key: value`（含列表项内的嵌套映射/序列）
- 标量：字符串（裸字符串 / 单引号 / 双引号）、整数、浮点、布尔、null/~

不支持（明确报错，带行号）：制表符缩进、流式集合 `[]` / `{}`、锚点 `&`/`*`、
别名、块标量 `|` / `>`、多文档分隔符 `---` / `...`、重复键。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
BOOL_VALUES = {
    "true": True,
    "True": True,
    "TRUE": True,
    "false": False,
    "False": False,
    "FALSE": False,
}
NULL_VALUES = {"null", "Null", "NULL", "~"}
UNSUPPORTED_PREFIXES = {
    "[": "流式序列（[]）",
    "{": "流式映射（{}）",
    "&": "锚点（&）",
    "*": "别名（*）",
    "|": "块标量（|）",
    ">": "块标量（>）",
    "---": "多文档分隔符（---）",
    "...": "多文档分隔符（...）",
    "%": "指令（%）",
    "@": "保留字符（@）",
    "`": "保留字符（`）",
}


class YamlSubsetError(ValueError):
    """解析失败。`lineno` 是 1-based 行号；消息里也带行号，便于直接定位。"""

    def __init__(self, lineno: int, message: str) -> None:
        self.lineno = lineno
        self.message = message
        super().__init__("第 %d 行: %s" % (lineno, message))


@dataclass(frozen=True)
class _Line:
    lineno: int
    indent: int
    text: str


def loads(text: str) -> Any:
    """解析一段 YAML 文本。根节点可以是映射或序列。"""
    lines = _tokenize(text)
    if not lines:
        raise YamlSubsetError(1, "文档为空")
    value, index = _parse_block(lines, 0, lines[0].indent)
    if index != len(lines):
        line = lines[index]
        raise YamlSubsetError(line.lineno, "缩进不一致或出现无法归属的内容")
    return value


def load(path) -> Any:
    """读文件并解析（UTF-8，BOM 容忍）。"""
    return loads(Path(path).read_text(encoding="utf-8-sig"))


# ------------------------------------------------------------------ 词法
def _tokenize(text: str) -> list[_Line]:
    lines: list[_Line] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        raw = raw.rstrip("\r")
        leading = raw[: len(raw) - len(raw.lstrip(" \t"))]
        if "\t" in leading:
            raise YamlSubsetError(lineno, "不支持制表符缩进，请改用空格")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text_part = raw[indent:]
        first = text_part[:1]
        is_sequence = first == "-" and (len(text_part) == 1 or text_part[1] == " ")
        # 序列项与映射键之外的语法在词法层先挡掉，错误信息比递归下降更直白
        if first in UNSUPPORTED_PREFIXES and not is_sequence:
            raise YamlSubsetError(
                lineno, "不支持 %s" % UNSUPPORTED_PREFIXES[first]
            )
        text_part = _strip_inline_comment(text_part, lineno).strip()
        if not text_part:
            continue
        lines.append(_Line(lineno=lineno, indent=indent, text=text_part))
    return lines


def _strip_inline_comment(text: str, lineno: int) -> str:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = None
        elif quote == "'":
            if char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 2                 # 单引号里的 '' 是转义，不结束字符串
                    continue
                quote = None
        elif char in {"'", '"'} and (index == 0 or text[index - 1] in {" ", ":", "-", "\t"}):
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in {" ", "\t"}):
            return text[:index].rstrip()
        index += 1
    return text


# ------------------------------------------------------------------ 语法
def _is_sequence_item(line: _Line) -> bool:
    return line.text == "-" or line.text.startswith("- ")


def _parse_block(lines: list[_Line], index: int, indent: int):
    line = lines[index]
    if _is_sequence_item(line):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(lines: list[_Line], index: int, indent: int):
    result: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise YamlSubsetError(line.lineno, "比同级键多缩进，无法归属到任何键")
        if _is_sequence_item(line):
            raise YamlSubsetError(line.lineno, "映射中不允许出现序列项（缺少键）")
        key, rest = _split_key(line)
        if key in result:
            raise YamlSubsetError(line.lineno, "重复的键: %s" % key)
        index += 1
        if rest == "":
            if index < len(lines) and lines[index].indent > indent:
                value, index = _parse_block(lines, index, lines[index].indent)
            else:
                value = None
        else:
            value = _parse_scalar(rest, line.lineno)
        result[key] = value
        if index < len(lines) and lines[index].indent > indent:
            nxt = lines[index]
            raise YamlSubsetError(nxt.lineno, "键 %s 的值已是标量，出现多余缩进" % key)
    return result, index


def _parse_sequence(lines: list[_Line], index: int, indent: int):
    result: list[Any] = []
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise YamlSubsetError(line.lineno, "比同级序列项多缩进，无法归属到任何项")
        if not _is_sequence_item(line):
            raise YamlSubsetError(line.lineno, "序列中不允许出现映射键（缺少 - 前缀）")
        rest = line.text[1:].strip()
        index += 1
        if rest == "":
            if index < len(lines) and lines[index].indent > indent:
                value, index = _parse_block(lines, index, lines[index].indent)
            else:
                value = None
        elif _looks_like_pair(rest):
            value, index = _parse_sequence_mapping(lines, index, indent, rest, line.lineno)
        else:
            value = _parse_scalar(rest, line.lineno)
            if index < len(lines) and lines[index].indent > indent:
                raise YamlSubsetError(
                    lines[index].lineno, "标量序列项后面不允许再有缩进内容"
                )
        result.append(value)
    return result, index


def _parse_sequence_mapping(lines: list[_Line], index: int, indent: int,
                            first_text: str, lineno: int):
    mapping: dict[str, Any] = {}
    key, rest = _split_key_text(first_text, lineno)
    if rest == "":
        if index < len(lines) and lines[index].indent > indent:
            value, index = _parse_block(lines, index, lines[index].indent)
        else:
            value = None
    else:
        value = _parse_scalar(rest, lineno)
    mapping[key] = value
    # `- key: v` 之后的同级键按下一行的缩进对齐（通常是 indent + 2）
    if index < len(lines) and lines[index].indent > indent:
        continuation_indent = lines[index].indent
        extra, index = _parse_mapping(lines, index, continuation_indent)
        for extra_key, extra_value in extra.items():
            if extra_key in mapping:
                raise YamlSubsetError(lineno, "序列项内重复的键: %s" % extra_key)
            mapping[extra_key] = extra_value
    return mapping, index


def _looks_like_pair(text: str) -> bool:
    try:
        _split_key_text(text, 1)
    except YamlSubsetError:
        return False
    return True


def _split_key(line: _Line):
    return _split_key_text(line.text, line.lineno)


def _split_key_text(text: str, lineno: int):
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = None
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == ":" and (index + 1 == len(text) or text[index + 1] in {" ", "\t"}):
            key = text[:index].strip()
            rest = text[index + 1:].strip()
            if not key:
                raise YamlSubsetError(lineno, "键不能为空")
            if key[0] in {"-", "[", "{", "|", ">"}:
                raise YamlSubsetError(lineno, "不支持的键名: %s" % key)
            return _unquote_key(key, lineno), rest
    raise YamlSubsetError(lineno, "缺少冒号，无法解析为映射键")


def _unquote_key(key: str, lineno: int) -> str:
    if key[0] in {"'", '"'}:
        if len(key) < 2 or key[-1] != key[0]:
            raise YamlSubsetError(lineno, "引号未闭合的键")
        return key[1:-1]
    return key


# ------------------------------------------------------------------ 标量
def _parse_scalar(text: str, lineno: int) -> Any:
    text = text.strip()
    if not text:
        return None
    if text[0] in {"[", "{", "|", ">", "&", "*", "%", "@", "`"}:
        raise YamlSubsetError(
            lineno, "不支持 %s" % UNSUPPORTED_PREFIXES[text[0]]
        )
    if text[0] == "'":
        return _parse_single_quoted(text, lineno)
    if text[0] == '"':
        return _parse_double_quoted(text, lineno)
    if text in BOOL_VALUES:
        return BOOL_VALUES[text]
    if text in NULL_VALUES:
        return None
    if INT_RE.match(text):
        return int(text)
    if FLOAT_RE.match(text):
        return float(text)
    if text[0] in {"[", "{"}:
        raise YamlSubsetError(lineno, "不支持流式集合（[] / {}）")
    return text


def _parse_single_quoted(text: str, lineno: int) -> str:
    if len(text) < 2 or text[-1] != "'":
        raise YamlSubsetError(lineno, "单引号字符串未闭合")
    inner = text[1:-1]
    out: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "'":
            out.append(char)
            index += 1
            continue
        if index + 1 < len(inner) and inner[index + 1] == "'":
            out.append("'")
            index += 2
            continue
        raise YamlSubsetError(lineno, "单引号字符串内出现未转义的引号")
    return "".join(out)


def _parse_double_quoted(text: str, lineno: int) -> str:
    if len(text) < 2 or text[-1] != '"':
        raise YamlSubsetError(lineno, "双引号字符串未闭合")
    inner = text[1:-1]
    out: list[str] = []
    index = 0
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r", "0": "\0"}
    while index < len(inner):
        char = inner[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        index += 1
        if index >= len(inner) or inner[index] not in escapes:
            raise YamlSubsetError(lineno, "不支持的转义序列")
        out.append(escapes[inner[index]])
        index += 1
    return "".join(out)
