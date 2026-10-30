"""分词与查询词处理（需求文档 §6.3）。

优先使用 jieba；未安装时退化为「中文按字 + 英文按词」，保证不因缺依赖而崩溃。
"""
import re

_jieba = None
_loaded = False

# 查询词里没信息量的词，去掉后能显著提升召回
STOPWORDS = {
    "怎么", "如何", "什么", "为什么", "哪些", "哪个", "如何学", "怎样",
    "的", "了", "是", "我", "想", "要", "能", "会", "和", "与", "及",
    "一下", "一个", "这个", "那个", "这些", "那些",
    "有没有", "请问", "帮我", "找", "搜", "推荐", "来点",
    "不知道", "开始", "但", "从", "哪", "学", "入门",
    "资料", "教程", "文章", "视频", "文档",
    "a", "an", "the", "to", "of", "and", "or", "is", "are", "how", "what",
}

PUNCT = re.compile(r"^[\s\W_]+$", re.UNICODE)
ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]*")
CJK = re.compile(r"[\u4e00-\u9fff]")


def _load():
    global _jieba, _loaded
    if _loaded:
        return _jieba
    _loaded = True
    try:
        import jieba
        jieba.setLogLevel(60)
        _jieba = jieba
    except Exception:
        _jieba = None
    return _jieba


def add_words(words):
    """把技术名词加进词典，避免 useEffect 之类被切碎。"""
    jb = _load()
    if not jb:
        return
    for w in words:
        w = w.strip()
        if w:
            jb.add_word(w)


def cut(text):
    """切词，返回去掉标点与空串的 token 列表（保留大小写，供展示）。"""
    if not text:
        return []
    jb = _load()
    if jb:
        raw = jb.cut_for_search(text)
    else:
        raw = _fallback_cut(text)
    out = []
    for t in raw:
        t = t.strip()
        if not t or PUNCT.match(t):
            continue
        out.append(t)
    return out


def _fallback_cut(text):
    """无 jieba 时的退化方案：ASCII 词整体保留，中文逐字。"""
    tokens, buf = [], ""
    for ch in text:
        if ASCII_WORD.match(ch):
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            if CJK.match(ch):
                tokens.append(ch)
            else:
                tokens.append(" ")
    if buf:
        tokens.append(buf)
    return tokens


def index_text(text):
    """写入索引用的空格串。"""
    return " ".join(cut(text))


def query_tokens(q):
    """查询词：切词 + 去停用词 + 去重保序 + 小写化。"""
    seen, out = set(), []
    for t in cut(q):
        low = t.lower()
        if low in STOPWORDS or len(low) == 0:
            continue
        if PUNCT.match(t):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
    # 多词查询里丢掉单字中文（"学"、"的"），它们只会拉低精度
    if len(out) > 1:
        out = [t for t in out if not (len(t) == 1 and "\u4e00" <= t <= "\u9fff")]
    return out
