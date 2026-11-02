"""目标槽位抽取（`RESOURCE_SEARCH` / `PLAN_CREATE` 的执行输入）。

**纯函数**：零网络、零数据库、零 import 上层 L0/L1 —— 可直接单测，也能在
`decide()` 这种同步纯函数里安全调用。

分工（§7.2 分层）：

- 规则先抽（本模块）：口语关键词 + 时间正则，覆盖「冲击前端岗位」「推荐点后端的资料」
  「12 周前端学习计划」这类说法；
- 规则**不全**且配了模型时，调用方再让 `router/classify.py::extract_slots_llm` 补抽，
  用 `merge_slots(rule, llm)` 合并。**规则永远优先**，模型只填空位。
- 没有模型也必须能用：调用方拿不到模型时直接用这里的结果，不报错（产品底线）。

`stack` 的取值必须与 DB `resources.stack` 一致（`frontend` / `backend` / `cs-basics` /
`interview` / `languages`），否则 SQL 过滤会一条都不命中。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: 站内技术栈（DB `resources.stack` 的实际取值集合；新增栈要同步这张表）。
STACKS = ("frontend", "backend", "cs-basics", "interview", "languages")

#: 难度三档（与 `schemas.models.Level` 一致）。
LEVELS = ("beginner", "intermediate", "advanced")

#: 只有方向、没有主题词时的检索/展示用词（`RESOURCE_SEARCH` 的查询词兜底）。
DIRECTION_LABEL = {
    "frontend": "前端",
    "backend": "后端",
    "cs-basics": "算法",
    "interview": "面试",
    "languages": "编程语言",
}

#: 口语方向词 → 站内栈名。**顺序即优先级**：一句话只落一个栈，先命中先算。
#: ASCII 词按"词边界"匹配（`_ascii_hit`），避免 js 命中 json、go 命中 good；
#: 中文按子串匹配（口语里技术名词常和助词连写，如「有没有讲React的」）。
_STACK_WORDS = (
    ("frontend", (
        "前端", "frontend", "网页", "react", "vue", "angular", "css", "html",
        "html5", "js", "javascript", "typescript", "ts", "jsx", "tsx", "dom",
        "flexbox", "布局", "浏览器",
    )),
    ("backend", (
        "后端", "backend", "服务端", "server", "java", "golang", "go", "python",
        "数据库", "sql", "mysql", "postgres", "postgresql", "sqlite", "redis",
        "接口", "fastapi", "http", "https", "docker", "nginx", "django", "flask",
        "spring", "rest", "restful", "微服务", "缓存",
    )),
    ("cs-basics", (
        "算法", "数据结构", "刷题", "leetcode", "排序", "链表", "二叉树", "红黑树",
        "树", "图论", "图", "动态规划", "复杂度", "递归", "队列", "栈", "哈希",
        "贪心", "二分",
    )),
    ("interview", ("面试", "interview", "八股", "面经", "校招", "社招")),
    ("languages", ("编程语言", "语言", "languages", "language")),
)

#: 难度词 → 三档。顺序 beginner → intermediate → advanced（先看"当前水平"的说法）。
_LEVEL_WORDS = (
    ("beginner", (
        "入门", "零基础", "新手", "小白", "初学者", "初学", "刚开始", "从零开始",
        "从零", "beginner", "初级",
    )),
    ("intermediate", (
        "进阶", "提升", "中级", "有基础", "有经验", "熟练", "提高", "intermediate",
    )),
    ("advanced", (
        "高级", "资深", "专家", "老手", "冲击", "冲刺", "advanced",
    )),
)

#: 这些词里的「树 / 图」不是数据结构（否则「地图」「树莓」会被判成 cs-basics）。
_TREE_NOISE = re.compile(r"(树莓|树叶|树立|树木|圣诞树)")
_GRAPH_NOISE = re.compile(r"(地图|图片|图书|图表|图纸|视图|试图|蓝图|宏图|构图|绘图|图像|版图)")

#: ASCII 词的边界：左边不能是字母/数字/+/#/_（`node.js` 的 `.` 不算粘连），
#: 右边同理。这样 `js` 不会命中 `json` / `jsx`，`go` 不会命中 `good`。
_ASCII_LEFT = r"(?<![a-z0-9+#_])"
_ASCII_RIGHT = r"(?![a-z0-9+#_])"

# --------------------------------------------------------------- 时间/投入
#: 与 `services/planner.py` 的 `regex_slots` 同一套思路（那边是计划接口的兜底，
#: 这边是槽位的规则层；不互相 import —— planner 已经 import 了 router，反向会成环）。
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"([0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十]+)"
_MONTHS_RE = re.compile(_NUM + r"\s*个?\s*月")
_WEEKS_RE = re.compile(_NUM + r"\s*(?:个)?\s*(?:周|星期)")
_DAILY_HOURS_RE = re.compile(r"每(?:天|日)\s*" + _NUM + r"\s*(?:个)?\s*小时")
_WEEKLY_HOURS_RE = re.compile(r"每\s*(?:周|星期|礼拜)\s*" + _NUM + r"\s*(?:个)?\s*小时")
_HALF_YEAR_RE = re.compile(r"半年")
_ONE_YEAR_RE = re.compile(r"(?:一|1)\s*年")
#: 从主题词里剥掉的时间片段（"12周"「每周 10 小时」不是主题）。
_TIME_NOISE = re.compile(
    _NUM + r"\s*(?:个)?\s*(?:周|星期|礼拜|月)"
    r"|每\s*(?:天|日|周|星期|礼拜)\s*" + _NUM + r"\s*(?:个)?\s*小时"
    r"|半年|(?:一|1)\s*年"
)

# --------------------------------------------------------------- 查询词清洗
#: 句首意图前缀 / 检索动词 / 口头填充 / 目标动作（剥到不再变化为止）。
#: 长词排前面（「我想学」先于「我想」、「搜索一下」先于「搜索」）。
_LEAD_WORDS = (
    "我想学", "我想要", "我想", "我要学", "我要", "我打算", "我准备", "我计划",
    "想要", "想学", "想", "帮我", "帮忙", "请", "麻烦", "给我", "求", "能", "可以",
)
_SEARCH_VERBS = (
    "搜索一下", "搜一下", "查一下", "找一下", "搜索", "查找", "查询", "搜", "查",
    "有没有", "推荐", "来几篇", "来几个", "来点", "介绍", "讲讲", "讲", "看看",
    "看", "找",
)
_FILLERS = (
    "一下", "一些", "一点", "关于", "有关", "讲的", "一个", "一份", "点", "些",
    "的", "说",
)
#: 目标动作：不是主题词（「冲击一下前端岗位」里的「冲击」）。
_GOAL_VERBS = (
    "冲击", "冲刺", "提升", "转行", "求职", "跳槽", "准备", "打算", "学习",
    "了解", "掌握", "精通", "做一个", "做个", "做", "排个", "制定", "安排",
    "规划", "学一下", "学", "练", "复习", "转",
)
#: 句尾泛指词（不是主题词）。
_TAIL_WORDS = (
    "学习计划", "学习路线", "学习路径", "复习计划", "复习大纲",
    "资料", "文章", "教程", "视频", "书籍", "书", "文档", "课程", "内容", "东西",
    "计划", "路线", "路径", "大纲", "课表", "排期", "规划", "方向", "工作",
)
#: 出现在任何位置都不构成主题词的噪声（「前端岗位」的「岗位」）。
_NOISE_ANY = re.compile(r"(岗位|相关|一些|东西)")
#: 清洗时要剪掉的首尾标点。
_TRIM = " \t\r\n的是了吗呢吧啊呀哦嘛。!！?？,，、;；:：·—~～"
#: 剥完以后仍不构成主题词的整词（避免「找点资料」→「点资料」这种残渣）。
_TOPIC_STOP = frozenset({
    "资料", "文章", "教程", "视频", "书", "文档", "课程", "内容", "东西", "学习",
    "了解", "掌握", "提升", "冲击", "冲刺", "转行", "求职", "准备", "打算",
    "计划", "路线", "大纲", "推荐", "介绍", "一下", "一些", "关于", "相关",
})


@dataclass
class SlotResult:
    """一条输入抽到的槽位。`complete` 只在 stack 或 topic 至少有一个时为 True。"""

    stack: str | None = None    # frontend / backend / cs-basics / interview / languages
    level: str | None = None    # beginner / intermediate / advanced
    goal: str | None = None     # 方向短语（≤120 字），给计划生成当目标
    topic: str | None = None    # 检索主题词，如「闭包」「链表」「React」
    weeks: int | None = None
    weekly_hours: int | None = None
    complete: bool = False
    source: str = "rule"        # "rule" | "llm"


# --------------------------------------------------------------- 入口
def extract_slots(text: str, *, locale: str = "zh-CN") -> SlotResult:
    """规则抽槽位。抽不到就留空，**不猜、不抛异常**。

    `locale` 目前不参与规则（中英关键词同表匹配），保留它是为了和
    `extract_slots_llm(text, llm, locale)` 的调用形状对称。
    """
    body = _norm(text)
    if not body:
        return SlotResult()

    stack = _extract_stack(body)
    level = _extract_level(body)
    weeks, weekly_hours = _extract_hours(body)
    topic = _extract_topic(body)
    # goal 是"方向短语"：有主题词用主题词，否则用栈的中文名（计划生成拿它做候选检索）
    goal = topic or DIRECTION_LABEL.get(stack or "")
    if goal:
        goal = goal[:120]

    return SlotResult(
        stack=stack,
        level=level,
        goal=goal,
        topic=topic,
        weeks=weeks,
        weekly_hours=weekly_hours,
        complete=bool(stack or topic),
        source="rule",
    )


def merge_slots(base: SlotResult, extra: SlotResult) -> SlotResult:
    """把 LLM 结果补进规则结果：**规则优先**，模型只填空位。

    `complete` 由合并后的 stack/topic 重新计算；`source` 标成 `"llm"` 当且仅当
    合并后至少有一个字段来自模型（便于日志里区分「规则够用」和「模型补过」）。
    """
    fields = ("stack", "level", "goal", "topic", "weeks", "weekly_hours")
    values: dict = {}
    used_extra = False
    for name in fields:
        left = getattr(base, name)
        right = getattr(extra, name)
        if left is None and right is not None:
            values[name] = right
            used_extra = True
        else:
            values[name] = left

    merged = SlotResult(**values, source="llm" if used_extra else base.source)
    merged.stack = _valid_stack(merged.stack)
    merged.level = _valid_level(merged.level)
    merged.complete = bool(merged.stack or merged.topic)
    return merged


# --------------------------------------------------------------- 规则实现
def _norm(text: str) -> str:
    """本模块是叶子（不 import 上层 L0）：只做 NFKC + 空白压缩兜底。

    调用方（`api/agent.py` / `router.decide`）通常已经过 `router.normalize()`。
    """
    if not text:
        return ""
    body = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", body).strip()


def _ascii_hit(word: str, lowered: str) -> bool:
    return re.search(_ASCII_LEFT + re.escape(word) + _ASCII_RIGHT, lowered) is not None


def _has_any(lowered: str, words) -> bool:
    for word in words:
        if word.isascii():
            if _ascii_hit(word, lowered):
                return True
        elif word in lowered:
            return True
    return False


def _extract_stack(body: str) -> str | None:
    lowered = body.lower()
    # 「地图/图片」这类词里的"图"不是数据结构：先剪掉再匹配。
    lowered = _GRAPH_NOISE.sub(" ", _TREE_NOISE.sub(" ", lowered))
    for stack, words in _STACK_WORDS:
        if _has_any(lowered, words):
            return stack
    return None


def _extract_level(body: str) -> str | None:
    lowered = body.lower()
    for level, words in _LEVEL_WORDS:
        if _has_any(lowered, words):
            return level
    return None


def _cn_number(raw: str) -> float | None:
    """中文/阿拉伯数字 → float（只支持 ≤99 的常用写法，周数/小时数够用）。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    if "十" in raw:
        head, _, tail = raw.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return float(tens * 10 + ones)
    if raw in _CN_DIGITS:
        return float(_CN_DIGITS[raw])
    return None


def _extract_hours(body: str) -> tuple[int | None, int | None]:
    """「12 周 / 三个月 / 半年 / 一年」→ weeks；「每周 10 小时 / 每天两小时」→ 每周小时。"""
    weeks = None
    months = _MONTHS_RE.search(body)
    if months:
        number = _cn_number(months.group(1))
        if number:
            weeks = int(round(number * 4))
    else:
        matched = _WEEKS_RE.search(body)
        if matched:
            number = _cn_number(matched.group(1))
            if number:
                weeks = int(number)
        elif _HALF_YEAR_RE.search(body):
            weeks = 24
        elif _ONE_YEAR_RE.search(body):
            weeks = 52

    weekly_hours = None
    daily = _DAILY_HOURS_RE.search(body)
    if daily:
        number = _cn_number(daily.group(1))
        if number:
            weekly_hours = int(round(number * 7))
    else:
        weekly = _WEEKLY_HOURS_RE.search(body)
        if weekly:
            number = _cn_number(weekly.group(1))
            if number:
                weekly_hours = int(number)

    if weeks is not None and not (1 <= weeks <= 520):
        weeks = None
    if weekly_hours is not None and not (1 <= weekly_hours <= 168):
        weekly_hours = None
    return weeks, weekly_hours


def _strip_level_words(text: str) -> str:
    """难度词（零基础/进阶/冲击……）由 `level` 槽位承载，不进检索词。"""
    body = text
    for _, words in _LEVEL_WORDS:
        for word in words:
            if word.isascii():
                body = re.sub(_ASCII_LEFT + re.escape(word) + _ASCII_RIGHT,
                              " ", body, flags=re.IGNORECASE)
            else:
                body = body.replace(word, " ")
    return body


def _strip_affixes(clause: str) -> str:
    """剥句首意图词与句尾泛指词（循环到不再变化；每轮至少剥掉一个片段才继续）。"""
    body = clause.strip(_TRIM)
    for _ in range(12):
        before = body
        body = body.strip(_TRIM)
        for word in _LEAD_WORDS + _SEARCH_VERBS + _FILLERS + _GOAL_VERBS:
            if body.startswith(word):
                body = body[len(word):]
                break
        body = body.strip(_TRIM)
        for word in _TAIL_WORDS:
            if body.endswith(word):
                body = body[: -len(word)]
                break
        body = body.strip(_TRIM)
        if body == before:
            break
    return body


def _clean_query(text: str) -> str:
    """把一句话压成检索词（`query_from_search_text` 的实现）。

    按标点切句，逐句剥前缀/后缀，删掉「岗位/相关/一些」这类噪声，最后按原顺序
    用空格拼回去。**方向词保留在这里**：调用方有槽位时会另行承载 stack，
    只有整句没有主题词时才把方向词当查询词（此时不存在"主题词 + 方向词"的叠加）。
    """
    body = _norm(text)
    cleaned: list[str] = []
    for clause in re.split(r"[，,。.!！?？;；、]+", body):
        # 周数/小时数、难度词由槽位单独承载，不进检索词
        clause = _strip_level_words(_TIME_NOISE.sub(" ", clause))
        piece = _NOISE_ANY.sub("", _strip_affixes(clause)).strip(_TRIM)
        if piece and piece not in _TOPIC_STOP:
            cleaned.append(piece)
    return " ".join(cleaned)


def clean_query(text: str) -> str:
    """公开的查询词清洗（`router.query_from_search_text` 委托到这里）。"""
    body = _norm(text)
    return _clean_query(body) or body


def _extract_topic(body: str) -> str | None:
    """检索主题词：清洗（已剥时间/难度/泛指词）后取第一个有意义的片段（≤120 字）。

    例：「有没有讲闭包的文章」→ `闭包`；「React 的资料」→ `React`；
    「给我推荐点后端的资料」→ `后端`；「冲击一下前端岗位，给我找点资料」→ `前端`。
    """
    cleaned = _clean_query(body)
    if not cleaned:
        return None
    cleaned = re.sub(r"\s+", " ", cleaned).strip(_TRIM)
    for piece in cleaned.split(" "):
        piece = piece.strip(_TRIM)
        if len(piece) < 2 or piece.lower() in _TOPIC_STOP:
            continue
        return piece[:120]
    return None


def _valid_stack(value: str | None) -> str | None:
    return value if value in STACKS else None


def _valid_level(value: str | None) -> str | None:
    return value if value in LEVELS else None
