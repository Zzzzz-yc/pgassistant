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
