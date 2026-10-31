"""L1 正则规则（§7.4，纯函数、零依赖、可单测）。

`OUT_OF_SCOPE` 与 `LEARNING` **逐字照抄**需求文档 §7.4：越界拒绝必须在 L1 完成，
绝不消耗 LLM 调用（§7.2）。改这两条规则等于改产品边界 → 必须先改需求文档。

`TASK_DELEGATION` 是 §7.3 示例「帮我写个快排」的补充闸门（M4 验收发现 §7.4 原正则
要求句中出现"代码/函数/…"这类**产物名**，"快排""贪心算法"这种说法抓不到）：
按"委托动词"识别，再用 `STUDY_INTENT` 排掉学习/计划类请求——**宁可漏拒，不可误拒**
「帮我做个前端学习计划」。
"""
from __future__ import annotations

import re

#: 越界：任务委托信号 + 代码产物（§7.4，逐字照抄，不得改）。
OUT_OF_SCOPE = re.compile(
    r"(帮我|替我|给我)(写|改|实现|生成|调试|修)(一下|个|段)?.*(代码|函数|组件|脚本|sql|正则|接口)",
    re.IGNORECASE,
)

#: 学习信号词：与越界同时命中时**降级为 RESOURCE_SEARCH**，不是拒绝（§7.4）。
LEARNING = re.compile(r"(怎么|如何|为什么|原理|入门|区别|是什么|学习|理解)")

#: 「任务委托」型越界（§7.3 的示例「帮我写个快排」）。
#: 只认"委托动词 + 短宾语"，长句与学习类请求交给 `STUDY_INTENT` 排除。
TASK_DELEGATION = re.compile(
    r"(帮我|替我|给我|帮忙)\s*(写|改|实现|生成|调试|修|做|画)"
)

#: 学习/计划意图词：出现在句子里就不按"任务委托"拒绝（§7.3 的 PLAN_CREATE / ADVICE / 检索）。
STUDY_INTENT = re.compile(
    r"(计划|路线|安排|建议|资料|学习|课程|教程|书|视频|文章|大纲|目标|怎么|如何|为什么|区别|原理)"
)

#: 委托动词之后最多允许这么多字；再长就不是"顺手要个产物"，而是真正的提问。
TASK_DELEGATION_MAX_TAIL = 30

#: 宾语开头常带的口语量词/指示词，判长度前先剥掉（"一个登录接口" → "登录接口"）。
_TAIL_PREFIXES = "一下个段一个这那的 位次份"

GREETING = re.compile(r"^(hi|hello|你好|嗨|在吗)[!！。.\s]*$", re.IGNORECASE)
CAPABILITY = re.compile(r"(你能做什么|会什么|怎么用|使用说明|帮助)")
SEARCH_INTENT = re.compile(r"(找|搜|有没有|推荐|来点).{0,10}(资料|教程|视频|书|文档|文章)")

# --------------------------------------------------------------- 计划创建（C2）
#: 第一组信号：规划名词 / 方向词。命中任一即说明"用户想要一份安排"。
PLAN_NOUN = re.compile(
    r"(计划|路线|大纲|课表|排期|学习路径|规划|从零|入门|学会|安排)"
)

#: 第一组的时间跨度信号。**"周"必须带数字或"每周/几周"**，否则"上周""周末""这周"
#: 这类时间词会和"学"凑成假阳性。
PLAN_HORIZON = re.compile(
    r"([0-9零一二两三四五六七八九十]+\s*(?:周|星期)"
    r"|每\s*(?:周|星期|礼拜)|几周|多少周|周数"
    r"|[0-9零一二两三四五六七八九十]+\s*个?\s*月|半年)"
)

#: 第二组：学习动作 / 身份信号（"学"排除"同学/大学/数学/学校"这类词的一部分）。
STUDY_SIGNAL = re.compile(r"(?<![同大学数])学|练|复习|面试|转行|刷题|背|啃")

#: 「只说想学什么」的主语/意愿词（信息不足时的方向性提问，C2）。
STUDY_GOAL_LEAD = re.compile(r"(我想|我要|我打算|我准备|想要|想|准备|打算|帮我|帮忙|给自己)")

#: 学习动作（含"入门/从零"这类方向词）。
STUDY_GOAL_VERB = re.compile(r"(?<![同大学数])学|练|复习|面试|转行|入门|从零|刷题")

#: 比较 / 征询类句子是 ADVICE（"学 Java 还是 Go"），不抢成"排计划"。
ADVICE_ONLY = re.compile(r"(还是|哪个|哪种|怎么|如何|区别|对比|建议|值不值|难吗|好不好|先学|优缺点)")

#: 方向性提问的选项里带上的主题词（"我想学前端" → "前端"）；抽不到就不带。
_TOPIC = re.compile(
    r"(?:学|练|复习|面试|转行|入门|从零)(?:一下|一点|习)?\s*"
    r"([A-Za-z0-9+#./\u4e00-\u9fff]{2,16})"
)
_TOPIC_TAIL = re.compile(r"(的|了|吧|呢|啊|呀|吗|计划|路线|安排|资料|课程|教程|专业|方向|一下|一点).*$")
_TOPIC_HEAD = re.compile(r"^(?:做|当|到|去|个|门)")

#: L0 的注入检测（§7.2 / §13.5）。命中 → L1 直接澄清，不把这段话送进模型。
#: 模型侧的防线是"候选资料只是数据"，但用户输入里的越权指令连送都不该送。
INJECTION = re.compile(
    r"(忽略(之前|以上|上面|前面)的?(所有)?(指令|提示|要求|设定)"
    r"|ignore\s+(all\s+)?(previous|above)\s+instructions?"
    r"|system\s*prompt"
    r"|你的(系统)?提示词"
    r"|你现在是"
    r"|you\s+are\s+now)",
    re.IGNORECASE,
)


def is_out_of_scope(text: str) -> bool:
    """越界判定：命中 OUT_OF_SCOPE **且**未命中 LEARNING（§7.4）。"""
    body = text or ""
    return bool(OUT_OF_SCOPE.search(body)) and not bool(LEARNING.search(body))


def is_task_delegation(text: str) -> bool:
    """§7.3 示例型越界：委托动词 + 1–30 字的短宾语，且整句没有学习/计划意图词。

    「帮我写个快排」「帮我写个 Python 脚本」→ True；
    「帮我做个前端学习计划」「帮我找闭包的资料」「帮我看看这个计划合不合理」→ False。
    """
    body = text or ""
    match = TASK_DELEGATION.search(body)
    if not match:
        return False
    if STUDY_INTENT.search(body):
        return False
    tail = body[match.end():].strip().lstrip(_TAIL_PREFIXES).strip()
    if not tail or len(tail) > TASK_DELEGATION_MAX_TAIL:
        return False
    return True


def is_greeting(text: str) -> bool:
    return bool(GREETING.match((text or "").strip()))


def is_capability(text: str) -> bool:
    return bool(CAPABILITY.search(text or ""))


def is_search_intent(text: str) -> bool:
    return bool(SEARCH_INTENT.search(text or ""))


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION.search(text or ""))


def is_plan_create(text: str) -> bool:
    """L1 的计划创建判定（C2）：**规划信号 + 学习信号**同时出现，零 LLM。

    正例：「帮我做个前端学习计划」「帮我安排一个 12 周的学习路线」「帮我制定面试复习大纲」。
    负例：「帮我写个快排」（没有学习/规划信号，越界规则先命中）、
    「有没有讲闭包的文章」（只有"找资料"）、「学 Java 还是 Go」（只有学习信号，没有规划信号）。
    """
    body = text or ""
    study = bool(STUDY_SIGNAL.search(body))
    plan = bool(PLAN_NOUN.search(body)) or bool(PLAN_HORIZON.search(body))
    return study and plan


def is_study_goal(text: str) -> bool:
    """只说了「想学什么」、没有任何规划信息（C2 的信息不足分支）。

    「我想学前端」「我要转行做后端」→ True（接着问方向 / 周数 / 每周投入）；
    「学 Java 还是 Go」「怎么学前端」→ False（这是 ADVICE，交给 L2）；
    「帮我做个前端学习计划」→ False（规划信号齐全，走 `is_plan_create`）。
    """
    body = text or ""
    if not STUDY_GOAL_LEAD.search(body):
        return False
    if not STUDY_GOAL_VERB.search(body):
        return False
    if ADVICE_ONLY.search(body):
        return False
    return not (PLAN_NOUN.search(body) or PLAN_HORIZON.search(body))


def study_topic(text: str) -> str:
    """从「我想学前端」里抽出方向词 `前端`，只用于澄清选项的措辞（抽不到返回空串）。"""
    match = _TOPIC.search(text or "")
    if not match:
        return ""
    topic = _TOPIC_TAIL.sub("", match.group(1)).strip()
    topic = _TOPIC_HEAD.sub("", topic).strip()
    return topic if 2 <= len(topic) <= 12 else ""
