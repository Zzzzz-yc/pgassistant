#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootstrap_index.py — 把 content/ 导入 SQLite 并自检检索。

用法:
  python backend/scripts/bootstrap_index.py --verify
  python backend/scripts/bootstrap_index.py --rebuild --verify
"""
import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT / ".deps"))   # 本地依赖目录（jieba）

from app.services.indexer import index_all, connect          # noqa: E402
from app.services.search import search                        # noqa: E402
from app.services import tokenizer                            # noqa: E402


def warmup():
    """预热 jieba 词典，避免第一次查询被 400ms 的加载时间拖累。"""
    t0 = time.perf_counter()
    tokenizer.cut("预热分词器")
    return (time.perf_counter() - t0) * 1000

VERIFY_QUERIES = [
    "闭包",
    "数组",
    "链表",
    "排序算法",
    "递归",
    "动态规划",
    "二叉树",
    "Java",
    "Python 入门",
    "HTTP 请求",
    "SQL 查询",
    "git 分支",
    "前端入门",
    "面试准备",
    "怎么学算法",
    "我想学 Java 但不知道从哪开始",
    "kafka 消息队列",          # 预期无结果：内容里确实没有
]


def print_stats(conn):
    rows = conn.execute(
        """SELECT stack, COUNT(*) n FROM resources WHERE is_deleted = 0
           GROUP BY stack ORDER BY n DESC"""
    ).fetchall()
    print("  技术栈:", " | ".join("%s %d" % (r["stack"], r["n"]) for r in rows))
    rows = conn.execute(
        """SELECT type, COUNT(*) n FROM resources WHERE is_deleted = 0
           GROUP BY type ORDER BY n DESC"""
    ).fetchall()
    print("  类型  :", " | ".join("%s %d" % (r["type"], r["n"]) for r in rows))
    total = conn.execute("SELECT COUNT(*) c FROM resources").fetchone()["c"]
    print("  条目  :", total)


def verify(conn):
    hits, misses = 0, 0
    for q in VERIFY_QUERIES:
        t0 = time.perf_counter()
        res = search(conn, q, limit=3)
        ms = (time.perf_counter() - t0) * 1000
        status = res["mode"]
        if res["items"]:
            hits += 1
            top = res["items"][0]
            print("  [%s] %-24s %5.1fms  %s  (%.2f)" % (status, q, ms, top["title"], top["score"]))
        else:
            misses += 1
            print("  [%s] %-24s %5.1fms  -- 无结果" % (status, q, ms))
    print("  命中 %d / 无结果 %d" % (hits, misses))
    return hits, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", default=str(PROJECT / "content"))
    ap.add_argument("--db", default=str(PROJECT / "data" / "app.db"))
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    t0 = time.perf_counter()
    result = index_all(Path(args.db), Path(args.content), rebuild=args.rebuild)
    cost = time.perf_counter() - t0

    if result.get("skipped"):
        print("索引已是最新，跳过（%.0fms）" % (cost * 1000))
    else:
        print("索引完成：%d 篇，%.2fs" % (result["files"], cost))
    print("数据库:", result["db"], "| 版本:", result["version"][:12])

    migration = result.get("migration") or {}
    if migration.get("applied"):
        print("schema 迁移：v%s → v%s（备份: %s）"
              % (migration.get("from"), migration.get("to"), migration.get("backup")))
    else:
        print("schema 版本：v%s（无需迁移）" % migration.get("to", "?"))

    conn = connect(Path(args.db))
    print_stats(conn)

    if args.verify:
        cost_ms = warmup()
        print("\n分词器预热: %.0fms" % cost_ms)
        print("检索自检：")
        verify(conn)


if __name__ == "__main__":
    main()
