"""L1 正则规则（§7.4，纯函数、零依赖、可单测）。

正则**逐字照抄**需求文档 §7.4：越界拒绝必须在 L1 完成，绝不消耗 LLM 调用（§7.2）。
改这条规则等于改产品边界 → 必须先改需求文档，不在这里"顺手放宽"。
"""
from __future__ import annotations

import re

#: 越界：任务委托信号 + 代码产物（§7.4）。
OUT_OF_SCOPE = re.compile(
    r"(帮我|替我|给我)(写|改|实现|生成|调试|修)(一下|个|段)?.*(代码|函数|组件|脚本|sql|正则|接口)",
    re.IGNORECASE,
)

#: 学习信号词：与越界同时命中时**降级为 RESOURCE_SEARCH**，不是拒绝（§7.4）。
LEARNING = re.compile(r"(怎么|如何|为什么|原理|入门|区别|是什么|学习|理解)")

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


def is_greeting(text: str) -> bool:
    return bool(GREETING.match((text or "").strip()))


def is_capability(text: str) -> bool:
    return bool(CAPABILITY.search(text or ""))


def is_search_intent(text: str) -> bool:
    return bool(SEARCH_INTENT.search(text or ""))


def looks_like_injection(text: str) -> bool:
    return bool(INJECTION.search(text or ""))
