"""路由可观测性统计：读 `data/logs/app.log`（JSON Lines）出中文报表。

日志由两处写出（都是 `log.info(json.dumps(...))`，被 `core/logging.py` 的 JSON Lines
formatter 包进 `message` 字段）：

- `router_l1`（`services/router/__init__.py:decide()`）：每个 L1 返回点一条，
  回答「哪条规则吃掉了这句话」；
- `router_decision`（`api/agent.py`）：一次请求一条最终决策，回答「最终判成什么、
  是规则还是模型判的、置信度多少」。

用法::

    python backend/scripts/router_stats.py
    python backend/scripts/router_stats.py --since 2026-11-02T20:00:00
    python backend/scripts/router_stats.py --json
    python backend/scripts/router_stats.py --log data/logs/app.log --low-confidence 0.7

`--since` 接受 ISO8601；不带时区时按**本机时区**理解（日志里的 `ts` 是 UTC）。
脚本对日志缺失、非 JSON 行、旧格式（纯文本）行、结构不合法的行一律跳过并计数，
不会中途抛异常。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

#: backend/scripts/router_stats.py -> backend -> 项目根
PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = PROJECT / "data" / "logs" / "app.log"

#: 只认这两种事件；其余（请求日志等）计为「非路由事件」。
DECISION_EVENT = "router_decision"
L1_EVENT = "router_l1"

#: L1 命中但置信度低于它 → 进「疑似误判候选」（study_goal 分支是 0.6）。
DEFAULT_LOW_CONFIDENCE = 0.8

#: 每类样本最多列几条 text_head，避免报表膨胀。
SAMPLE_LIMIT = 5


def parse_time(value: str | None) -> datetime | None:
    """ISO8601 → UTC aware datetime；空值/坏值返回 None。"""
    text = (value or "").strip()
    if not text:
        return None
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:              # 没带时区 → 按本机时区
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc)


def _record_of(raw: str) -> dict | None:
    """一行日志 → 事件 dict。非 JSON / 结构不合法的行返回 None（计入跳过）。"""
    try:
        outer = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(outer, dict):
        return None

    message = outer.get("message")
    if isinstance(message, str) and message.lstrip().startswith("{"):
        try:
            inner = json.loads(message)
        except ValueError:
            inner = None
        if isinstance(inner, dict):
            if "ts" not in inner and outer.get("ts"):
                inner["ts"] = outer["ts"]
            return inner
    if "event" in outer:                   # 兼容直接写 event 的旧行
        return outer
    return {}                              # 合法 JSON，但不是路由事件


def read_log(path: Path, *, since: datetime | None = None) -> tuple[list[dict], dict]:
    """读日志 → (路由事件列表, 计数)。文件缺失时返回空列表 + `missing=True`。"""
    stats = {
        "total_lines": 0,
        "skipped": 0,
        "ignored": 0,
        "filtered": 0,
        "undated": 0,
        "missing": False,
    }
    records: list[dict] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        stats["missing"] = True
        return records, stats

    with handle:
        for line in handle:
            if not line.strip():
                continue
            stats["total_lines"] += 1
            record = _record_of(line.strip())
            if record is None:
                stats["skipped"] += 1
                continue
            if not record:
                stats["ignored"] += 1
                continue
            event = record.get("event")
            if event not in (DECISION_EVENT, L1_EVENT):
                stats["ignored"] += 1
                continue
            moment = parse_time(record.get("ts")) if since is not None else None
            if since is not None:
                if moment is None:
                    stats["undated"] += 1
                    continue
                if moment < since:
                    stats["filtered"] += 1
                    continue
            records.append(record)
    return records, stats


def _confidence(record: dict) -> float | None:
    try:
        return float(record.get("confidence"))
    except (TypeError, ValueError):
        return None


def _intent(record: dict) -> str:
    return str(record.get("intent") or "?")


def _source(record: dict) -> str:
    return str(record.get("source") or "?")


def _rule(record: dict) -> str | None:
    rule = record.get("rule")
    return str(rule) if rule not in (None, "") else None


def collect(records: list[dict], *, low_confidence: float = DEFAULT_LOW_CONFIDENCE) -> dict:
    """把路由事件汇总成统计结构（report 的 JSON 形态）。"""
    decisions = [r for r in records if r.get("event") == DECISION_EVENT]
    l1_events = [r for r in records if r.get("event") == L1_EVENT]

    by_source = Counter(_source(r) for r in decisions)
    by_intent = Counter(_intent(r) for r in decisions)

    by_rule: dict[str, dict] = {}
    for record in decisions:
        rule = _rule(record)
        if rule is None:
            continue
        bucket = by_rule.setdefault(rule, {"count": 0, "intents": Counter()})
        bucket["count"] += 1
        bucket["intents"][_intent(record)] += 1

    # 疑似误判 1：L1 命中但置信度低（study_goal 的 0.6 是最典型的一条）。
    low_confidence_hits = []
    for record in decisions:
        rule = _rule(record)
        confidence = _confidence(record)
        if rule is None or confidence is None:
            continue
        if confidence < low_confidence:
            low_confidence_hits.append({
                "rule": rule,
                "intent": _intent(record),
                "confidence": confidence,
                "text_head": str(record.get("text_head") or ""),
            })

    # 疑似误判 2：同一条规则判出多种意图 → 至少有一次是错的（规则该收窄或拆开）。
    mixed_rule_intents = []
    for rule, bucket in sorted(by_rule.items()):
        if len(bucket["intents"]) < 2:
            continue
        samples: dict[str, list[str]] = defaultdict(list)
        for record in decisions:
            if _rule(record) != rule:
                continue
            intent = _intent(record)
            head = str(record.get("text_head") or "")
            if head and len(samples[intent]) < SAMPLE_LIMIT:
                samples[intent].append(head)
        mixed_rule_intents.append({
            "rule": rule,
            "count": bucket["count"],
            "intents": dict(bucket["intents"]),
            "samples": dict(samples),
        })

    # 未命中走 L2：source=llm|fallback 的样本，按意图分组列 text_head。
    l2_groups: dict[str, list[str]] = defaultdict(list)
    l2_by_intent: Counter = Counter()
    l2_count = 0
    for record in decisions:
        if _source(record) not in ("llm", "fallback"):
            continue
        l2_count += 1
        intent = _intent(record)
        l2_by_intent[intent] += 1
        if len(l2_groups[intent]) < SAMPLE_LIMIT:
            head = str(record.get("text_head") or "")
            if head:
                l2_groups[intent].append(head)

    return {
        "total_decisions": len(decisions),
        "l1_events": len(l1_events),
        "by_source": dict(sorted(by_source.items())),
        "by_intent": dict(sorted(by_intent.items())),
        "by_rule": {
            rule: {"count": bucket["count"], "intents": dict(bucket["intents"])}
            for rule, bucket in sorted(by_rule.items())
        },
        "low_confidence_threshold": low_confidence,
        "low_confidence_hits": low_confidence_hits,
        "mixed_rule_intents": mixed_rule_intents,
        "l2_count": l2_count,
        "l2_by_intent": dict(sorted(l2_by_intent.items())),
        "l2_samples": {intent: heads for intent, heads in sorted(l2_groups.items())},
    }


def _pct(count: int, total: int) -> str:
    if not total:
        return "0.0%"
    return f"{count * 100 / total:.1f}%"


def render_text(report: dict, stats: dict, *, log_path: Path,
                since_text: str | None) -> str:
    """中文报表（人读）。"""
    total = report["total_decisions"]
    lines: list[str] = []
    lines.append("路由日志统计（router_observability）")
    lines.append(f"日志: {log_path}")
    lines.append(f"统计起点: {since_text or '全量'}")
    lines.append(
        f"总行数: {stats['total_lines']}｜跳过: {stats['skipped']}（非 JSON / 结构不合法）"
        f"｜非路由事件: {stats['ignored']}｜早于起点: {stats['filtered']}"
    )
    lines.append(
        f"总决策数: {total}（L1 事件 {report['l1_events']} 条）"
    )
    lines.append("")

    lines.append("── 按 source ──")
    if report["by_source"]:
        for source, count in report["by_source"].items():
            lines.append(f"  {source}: {count} ({_pct(count, total)})")
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append("── 按 intent ──")
    if report["by_intent"]:
        for intent, count in report["by_intent"].items():
            lines.append(f"  {intent}: {count} ({_pct(count, total)})")
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append("── L1 各规则命中 ──")
    if report["by_rule"]:
        for rule, bucket in report["by_rule"].items():
            intents = "、".join(
                f"{intent}={count}" for intent, count in bucket["intents"].items()
            )
            lines.append(f"  {rule}: {bucket['count']} 次 → {intents}")
    else:
        lines.append("  （无 L1 命中）")
    lines.append("")

    lines.append(f"── 疑似误判候选：L1 命中但置信度 < {report['low_confidence_threshold']:.2f} ──")
    if report["low_confidence_hits"]:
        for item in report["low_confidence_hits"]:
            lines.append(
                f"  {item['rule']} → {item['intent']} conf={item['confidence']:.2f}"
                f" 「{item['text_head']}」"
            )
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append("── 疑似误判候选：同一规则判成多种意图 ──")
    if report["mixed_rule_intents"]:
        for item in report["mixed_rule_intents"]:
            intents = "、".join(
                f"{intent}={count}" for intent, count in item["intents"].items()
            )
            lines.append(f"  {item['rule']}: {item['count']} 次 → {intents}")
            for intent, heads in item["samples"].items():
                samples = "」「".join(heads)
                lines.append(f"    {intent}: 「{samples}」")
    else:
        lines.append("  （无）")
    lines.append("")

    lines.append(f"── 未命中走 L2：{report['l2_count']} 条 ──")
    if report["l2_by_intent"]:
        for intent, count in report["l2_by_intent"].items():
            heads = report["l2_samples"].get(intent, [])
            samples = "」「".join(heads) if heads else "（无样本）"
            lines.append(f"  {intent}: {count} 条，样本: 「{samples}」")
    else:
        lines.append("  （无）")
    return "\n".join(lines)


def build_report(records: list[dict], stats: dict, *, log_path: Path,
                 since_text: str | None,
                 low_confidence: float = DEFAULT_LOW_CONFIDENCE) -> dict:
    report = collect(records, low_confidence=low_confidence)
    report.update({
        "log": str(log_path),
        "since": since_text,
        "total_lines": stats["total_lines"],
        "skipped": stats["skipped"],
        "ignored": stats["ignored"],
        "filtered": stats["filtered"],
        "undated": stats["undated"],
        "missing": stats["missing"],
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="统计路由日志（router_l1 / router_decision）",
    )
    parser.add_argument("--log", default=str(DEFAULT_LOG),
                        help=f"日志文件路径（默认 {DEFAULT_LOG}）")
    parser.add_argument("--since", default="",
                        help="只统计该 ISO8601 时间之后的记录（不带时区按本机时区）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--low-confidence", type=float, default=DEFAULT_LOW_CONFIDENCE,
                        help=f"L1 低置信度阈值（默认 {DEFAULT_LOW_CONFIDENCE}）")
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    since = parse_time(args.since)
    if args.since and since is None:
        print(f"--since 不是合法 ISO8601 时间: {args.since}", file=sys.stderr)
        return 2

    records, stats = read_log(log_path, since=since)
    if stats["missing"]:
        print(f"日志文件不存在: {log_path}", file=sys.stderr)
        return 1

    report = build_report(
        records, stats,
        log_path=log_path,
        since_text=(since.isoformat() if since else None),
        low_confidence=args.low_confidence,
    )
    if args.json:
        report["records"] = records
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report, stats, log_path=log_path,
                          since_text=(since.isoformat() if since else None)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
