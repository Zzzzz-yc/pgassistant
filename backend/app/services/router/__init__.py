"""L0 规范化 + L1 正则短路（§7.2 分层路由的前两层）。

调用顺序固定：

    normalize(text) → decide(text, locale)   # 纯函数，零网络、零数据库
                     ↓ source == "fallback" 时
                     classify(text, llm)     # L2，见 router/classify.py

**越界请求在这里就结束**，绝不进入 L2（§7.2「越界拒绝必须在 L1 完成」/ §7.7
「不允许模型自行发挥拒绝文案」）。话术是模块常量，zh-CN 与 en-US 各一份。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ...schemas.models import Intent, PlanSlots
from . import rules

# --------------------------------------------------------------- 常量话术
#: §7.7 的统一拒绝话术（zh-CN 原文逐字照抄）。
REFUSAL_ZH = (
    "我只负责这个学习库里的资料检索、学习建议和学习计划，写代码/调试不在我的范围内。\n"
    "你可以换个问法，比如：「前端要学哪些东西」「有没有讲闭包的文章」。"
)
REFUSAL_EN = (
    "I only cover resource search, study advice and study plans from this library — "
    "writing or debugging code is out of scope.\n"
    "Try asking something like: \"What should I learn for frontend?\" or "
    "\"Any articles on closures?\""
)

GREETING_ZH = (
    "你好！我是这个学习库的助手。我能帮你找站内资料、给学习建议、生成和调整学习计划。"
)
GREETING_EN = (
    "Hi! I'm the assistant for this library. I can find resources, give study advice, "
    "and draft or adjust study plans."
)

CAPABILITY_ZH = (
    "我负责四件事：找资料、给学习建议、生成学习计划、按你的情况调整计划。\n"
    "可以试试：「有没有讲闭包的文章」「帮我做个 12 周前端计划」「学 Java 还是 Go」。\n"
    "写代码、调试、翻译和站外知识问答不在我的范围内。"
)
CAPABILITY_EN = (
    "I do four things: find resources, give study advice, draft study plans, and adjust "
    "a plan to your situation.\n"
    "Try: \"Any articles on closures?\", \"Make me a 12-week frontend plan\", "
    "\"Java or Go?\".\n"
    "Writing code, debugging, translation and general knowledge are out of scope."
)

CLARIFY_ZH = (
    "我没太确定你想做什么。可以点一个方向，或换个说法再问一次："
)
CLARIFY_EN = (
    "I'm not sure what you need. Pick a direction below, or rephrase your question:"
)

#: 「只说想学什么」时的**方向性提问**（C2）。不再用上面那句通用的「我不确定」。
#: 与拒绝话术一样是模块常量：模型不参与生成（§7.7）。
PLAN_CLARIFY_ZH = (
    "要不要我帮你排一个学习计划？大概学什么方向、多少周、每周能投入几小时？"
)
PLAN_CLARIFY_EN = (
    "Want me to draft a study plan for you? Which direction are you aiming for, "
    "roughly how many weeks, and how many hours a week can you put in?"
)

#: 澄清卡片的可点选项（§7.2 兜底：给 3 个可点选项，最多 3 个）。
CLARIFY_OPTIONS_ZH = (
    "找资料，例如「有没有讲闭包的文章」",
    "生成学习计划，例如「12 周前端入门计划」",
    "学习建议，例如「学 Java 还是 Go」",
)
CLARIFY_OPTIONS_EN = (
    "Find resources, e.g. \"articles on closures\"",
    "Draft a study plan, e.g. \"12-week frontend plan\"",
    "Study advice, e.g. \"Java or Go?\"",
)

#: 计划方向性提问的第三个选项（前两条要拼方向词，见 `plan_clarify_options`）。
PLAN_CLARIFY_OPTIONS_ZH_TAIL = "给我学习路线建议"
PLAN_CLARIFY_OPTIONS_EN_TAIL = "Give me a study roadmap"


def _t(zh: str, en: str, locale: str) -> str:
    return en if (locale or "").lower().startswith("en") else zh


def refusal(locale: str = "zh-CN") -> str:
    """§7.7 的拒绝话术常量（L1 命中后直接返回，不经过模型）。"""
    return _t(REFUSAL_ZH, REFUSAL_EN, locale)


def greeting_text(locale: str = "zh-CN") -> str:
    return _t(GREETING_ZH, GREETING_EN, locale)


def capability_text(locale: str = "zh-CN") -> str:
    return _t(CAPABILITY_ZH, CAPABILITY_EN, locale)


def clarify_text(locale: str = "zh-CN") -> str:
    return _t(CLARIFY_ZH, CLARIFY_EN, locale)


def clarify_options(locale: str = "zh-CN") -> list[str]:
    return list(CLARIFY_OPTIONS_EN if (locale or "").lower().startswith("en")
                else CLARIFY_OPTIONS_ZH)


def plan_clarify_text(locale: str = "zh-CN") -> str:
    """「只说想学什么」的方向性提问（C2，常量话术，不经模型）。"""
    return _t(PLAN_CLARIFY_ZH, PLAN_CLARIFY_EN, locale)


def plan_clarify_options(locale: str = "zh-CN", topic: str = "") -> list[str]:
    """顺着用户话头的三条可点选项（C2，§7.2 最多 3 个）。

    `topic` 是 `rules.study_topic()` 抽到的方向词（例如「前端」）；抽不到就用通用措辞，
    不编造方向。
    """
    topic = (topic or "").strip()
    if (locale or "").lower().startswith("en"):
        return [
            f"Make a 12-week {topic} plan" if topic else "Make a 12-week study plan",
            f"Find beginner {topic} resources" if topic else "Find beginner resources",
            PLAN_CLARIFY_OPTIONS_EN_TAIL,
        ]
    return [
        f"生成 12 周{topic}计划" if topic else "生成 12 周学习计划",
        f"先找{topic}入门资料" if topic else "先找入门资料",
        PLAN_CLARIFY_OPTIONS_ZH_TAIL,
    ]


# --------------------------------------------------------------- L0 规范化
#: 零宽 / 方向控制字符：模型看不到，但能骗过关键词匹配，先删掉。
_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
     0x2060, 0x2061, 0x2062, 0x2063, 0xFEFF],
    None,
)
#: C0/C1 控制字符（保留 \n 与 \t，稍后压成单空格）。
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WS = re.compile(r"\s+")


def normalize(text: str, *, max_len: int = 500) -> str:
    """L0：去零宽/控制字符 → 全角转半角（NFKC）→ 压空白 → 截断到 `max_len`。

    截断是**产品规格**（§7.2：>500 字截断，不报错），所以超过 500 字是 200 而不是 422。
    """
    if not text:
        return ""
    body = str(text).translate(_ZERO_WIDTH)
    body = _CONTROL.sub("", body)
    body = unicodedata.normalize("NFKC", body)
    body = _WS.sub(" ", body).strip()
    if max_len and len(body) > max_len:
        body = body[:max_len].strip()
    return body


# --------------------------------------------------------------- L1 判定
@dataclass(frozen=True)
class RouteDecision:
    """L1/L2 的判定结果（契约签名，字段不得删改）。"""

    intent: Intent
    confidence: float
    source: str                      # "rule" | "llm" | "fallback"
    slots: PlanSlots | None = None
    needs_clarify: bool = False
    clarify_options: list[str] = field(default_factory=list)


def decide(text: str, *, locale: str = "zh-CN") -> RouteDecision:
    """只跑 L1。命中即返回；**没命中时返回 `source="fallback"`**，由调用方决定是否进 L2。

    优先级：OUT_OF_SCOPE（原正则）| TASK_DELEGATION > 注入 > GREETING/CAPABILITY
    > RESOURCE_SEARCH > PLAN_CREATE（C2）> L2。注入检测排在越界之后、问候之前：
    命中就不把这段输入送进模型（§13.5）。

    PLAN_CREATE 放在 RESOURCE_SEARCH 之后：「帮我找学习计划的资料」仍按找资料执行，
    不抢成"生成计划"。检索/问候/能力/计划判定与越界一样**零 LLM**。
    """
    body = normalize(text)
    if not body:
        return RouteDecision(
            intent=Intent.CLARIFY, confidence=0.0, source="fallback",
            needs_clarify=True, clarify_options=clarify_options(locale),
        )

    # §7.3 的「帮我写个快排」型委托：与 §7.4 原正则同优先级，同样零 LLM。
    if rules.is_out_of_scope(body) or rules.is_task_delegation(body):
        return RouteDecision(intent=Intent.OUT_OF_SCOPE, confidence=1.0, source="rule")
    if rules.looks_like_injection(body):
        return RouteDecision(
            intent=Intent.CLARIFY, confidence=0.0, source="rule",
            needs_clarify=True, clarify_options=clarify_options(locale),
        )
    if rules.is_greeting(body):
        return RouteDecision(intent=Intent.GREETING, confidence=1.0, source="rule")
    if rules.is_capability(body):
        return RouteDecision(intent=Intent.CAPABILITY, confidence=1.0, source="rule")
    if rules.is_search_intent(body):
        return RouteDecision(intent=Intent.RESOURCE_SEARCH, confidence=1.0, source="rule")
    # C2：计划创建信号齐全 → 直接判 PLAN_CREATE，不再先丢给 L2。
    if rules.is_plan_create(body):
        return RouteDecision(intent=Intent.PLAN_CREATE, confidence=1.0, source="rule")
    # C2：只说了「想学什么」（没有方向/周数/每周投入）→ PLAN_CREATE + 方向性提问。
    if rules.is_study_goal(body):
        return RouteDecision(
            intent=Intent.PLAN_CREATE,
            confidence=0.6,
            source="rule",
            needs_clarify=True,
            clarify_options=plan_clarify_options(locale, rules.study_topic(body)),
        )

    return RouteDecision(intent=Intent.CLARIFY, confidence=0.0, source="fallback")


# --------------------------------------------------------------- 检索查询词
_LEAD = re.compile(r"^(帮我|帮忙|请|麻烦|我想|想|能|可以)?\s*")
_VERB = re.compile(r"^(找|搜|搜索|搜一下|查|查一下|有没有|推荐|来点|来几个|介绍|讲讲)\s*")
_FILLER = re.compile(r"^(一下|一些|点|关于|有关|讲|说|介绍|的)\s*")
_TAIL = re.compile(r"(的)?(资料|教程|视频|书|书籍|文档|文章|课程|内容)[。!！?？\s]*$")
_TRIM = "的是了吗呢 。!！?？,，、"


def query_from_search_text(text: str) -> str:
    """把「有没有讲闭包的文章」压成检索词「闭包」（§7.3 的 RESOURCE_SEARCH 执行器）。

    抽不干净就退回整句——宁可用整句去搜，也不要空查询。
    """
    body = normalize(text)
    cleaned = _LEAD.sub("", body)
    cleaned = _VERB.sub("", cleaned)
    cleaned = _FILLER.sub("", cleaned)
    cleaned = _TAIL.sub("", cleaned)
    cleaned = cleaned.strip(_TRIM)
    return cleaned or body
