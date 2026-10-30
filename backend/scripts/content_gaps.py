#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""content_gaps.py — 主题覆盖体检：跑一批典型查询，找出内容缺口。

用法: python backend/scripts/content_gaps.py --db data/app.db
输出: JSON（stdout）+ docs/gap-probe.json
"""
import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT / ".deps"))

from app.services.indexer import connect      # noqa: E402
from app.services.search import search        # noqa: E402

PROBES = {
    "前端/HTML-CSS": ["HTML 语义化", "CSS Flexbox", "CSS Grid", "响应式布局", "CSS 变量"],
    "前端/JavaScript": ["闭包", "原型链", "Promise", "async await", "事件循环", "DOM 操作",
                       "模块化 import export", "this 指向", "数组方法 map filter"],
    "前端/工程化": ["npm 包管理", "Vite 构建", "webpack", "TypeScript 类型", "ESLint"],
    "前端/框架": ["React 组件", "React Hooks", "Vue 响应式", "状态管理", "路由"],
    "后端/网络": ["HTTP 状态码", "HTTPS TLS", "CORS 跨域", "TCP 三次握手", "DNS"],
    "后端/接口": ["REST 设计", "GraphQL", "JWT 认证", "接口幂等", "分页设计"],
    "后端/数据库": ["数据库索引", "事务隔离级别", "SQL JOIN", "慢查询优化", "Redis 缓存", "NoSQL"],
    "后端/部署": ["Docker 镜像", "Nginx 反向代理", "CI CD", "日志收集", "负载均衡"],
    "语言/Java": ["Java 集合", "Java 多线程", "JVM 内存模型", "Java 泛型", "Spring Boot"],
    "语言/Python": ["Python 装饰器", "列表推导", "虚拟环境", "生成器", "asyncio"],
    "语言/其他": ["Go 协程", "Rust 所有权", "C 指针", "类型系统"],
    "算法": ["并查集", "线段树", "KMP 字符串匹配", "拓扑排序", "最短路 Dijkstra", "单调栈"],
    "面试": ["系统设计", "行为面试", "简历写法", "算法复杂度", "时间复杂度"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(PROJECT / "data" / "app.db"))
    ap.add_argument("--out", default=str(PROJECT / "docs" / "gap-probe.json"))
    args = ap.parse_args()

    conn = connect(Path(args.db))
    report, total_hit, total_miss = {}, 0, 0

    for group, queries in PROBES.items():
        rows = []
        for q in queries:
            res = search(conn, q, limit=3)
            hit = bool(res["items"])
            if hit:
                total_hit += 1
            else:
                total_miss += 1
            rows.append({
                "query": q,
                "hit": hit,
                "mode": res["mode"],
                "top": res["items"][0]["title"] if hit else None,
            })
        report[group] = rows
        miss = [r["query"] for r in rows if not r["hit"]]
        print("%-14s 命中 %d/%d %s" % (group, len(rows) - len(miss), len(rows),
                                       ("缺: " + "、".join(miss)) if miss else ""))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n覆盖率: %d/%d = %.0f%%" % (total_hit, total_hit + total_miss,
                                       100 * total_hit / (total_hit + total_miss)))


if __name__ == "__main__":
    main()
