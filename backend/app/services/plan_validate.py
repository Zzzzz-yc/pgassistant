"""计划草稿校验（§7.6 第三段；与模板、AI、手动三条路径共用）。

分工：
- `schemas.models.PlanDraft` 管**结构规则**（字段范围、阶段数、周数偏差）；
- 本模块管**业务规则**：白名单、总条目数、总时长偏差、难度倒挂、估时缺失。

硬规则违反 → `PlanInvalid`（→ 422 `PLAN_INVALID`，带 `detail.errors` 与
`detail.suggestions`）；软规则只进 warnings，不拦保存。
"""
from __future__ import annotations

from ..schemas.models import PlanDraft

#: 难度排序（数值越大越难）。`advanced` 不得出现在 `beginner` 阶段之前。
LEVEL_RANK = {"beginner": 0, "intermediate": 1, "advanced": 2}

#: 总时长与 `weeks × weekly_hours × 60` 的允许偏差（§7.6 第三段）。
DURATION_TOLERANCE = 0.30

#: 整份计划的总条目数区间（§7.6 / API 3.10）。
MIN_ITEMS, MAX_ITEMS = 5, 40

#: 题注含这两个字时，允许该条目突破难度顺序（§7.6「除非标注选学」）。
EXEMPT_MARK = "选学"


class PlanInvalid(Exception):
    """业务规则不通过。`errors` 逐条给人看；`suggestions` 是 §7.6 的失败兜底素材。"""

    def __init__(self, errors: list[str], suggestions: list[dict] | None = None) -> None:
        self.errors = list(errors)
        self.suggestions = list(suggestions) if suggestions else []
        super().__init__("；".join(self.errors))


def validate_draft(
    draft: PlanDraft,
    *,
    allowed_slugs: set[str] | None = None,
    level_by_slug: dict[str, str] | None = None,
    target_minutes: float | None = None,
) -> tuple[PlanDraft, list[str]]:
    """校验并清洗草稿，返回 `(清洗后的草稿, warnings)`。

    `allowed_slugs is None` 时跳过白名单（模板信任，见 MODULES §4.1）；
    `level_by_slug is None` 时跳过难度顺序；`target_minutes is None` 时按
    `weeks × weekly_hours × 60` 现算。
    """
    warnings: list[str] = []
    cleaned = draft
    if allowed_slugs is not None:
        cleaned, whitelist_warnings = check_whitelist(cleaned, allowed_slugs)
        warnings.extend(whitelist_warnings)

    errors: list[str] = []
    errors.extend(check_counts(cleaned))
    target = (
        target_minutes
        if target_minutes is not None
        else cleaned.weeks * cleaned.weekly_hours * 60.0
    )
    errors.extend(check_duration(cleaned, target))
    if level_by_slug is not None:
        errors.extend(check_level_order(cleaned, level_by_slug))

    # 软规则：估时缺失按 30 分钟计，只记 warning（§7.6 第三段最后一条）
    for stage_index, stage in enumerate(cleaned.stages, start=1):
        for item_index, item in enumerate(stage.items, start=1):
            if "est_minutes" not in item.model_fields_set:
                warnings.append(
                    "第 %d 阶段第 %d 条缺少 est_minutes，按 %d 分钟计"
                    % (stage_index, item_index, item.est_minutes)
                )

    if errors:
        raise PlanInvalid(errors)
    return cleaned, warnings


def check_whitelist(draft: PlanDraft, allowed: set[str]) -> tuple[PlanDraft, list[str]]:
    """不在白名单的 `resource_slug` → **丢弃整个条目**（不是整份失败）。"""
    warnings: list[str] = []
    new_stages = []
    dropped = 0
    for stage_index, stage in enumerate(draft.stages, start=1):
        kept = []
        for item in stage.items:
            slug = item.resource_slug
            if slug is None or slug in allowed:
                kept.append(item)
                continue
            dropped += 1
            warnings.append(
                "第 %d 阶段「%s」引用了不存在的 slug: %s（已丢弃该条目）"
                % (stage_index, stage.title, slug)
            )
        new_stages.append(stage.model_copy(update={"items": kept}))
    if dropped:
        warnings.append("共丢弃 %d 条引用了未知 slug 的条目" % dropped)
    # model_copy(update=...) 不重新校验：这里只删条目，结构只会变宽松
    return draft.model_copy(update={"stages": new_stages}), warnings


def fill_estimates(draft: PlanDraft, duration_by_slug: dict[str, int]) -> int:
    """按资料 `duration_min` 填充缺失的 `est_minutes`，返回填充条数。

    只填「请求里没写」的条目（`model_fields_set` 判定），不覆盖用户/模板
    显式给出的估时。`est_minutes` 模型下限是 10，所以此处夹到 10–240。
    """
    filled = 0
    for stage in draft.stages:
        for item in stage.items:
            if item.resource_slug is None or "est_minutes" in item.model_fields_set:
                continue
            minutes = duration_by_slug.get(item.resource_slug)
            if minutes is None:
                continue
            item.est_minutes = max(10, min(240, int(minutes)))
            item.model_fields_set.add("est_minutes")
            filled += 1
    return filled


def check_counts(draft: PlanDraft) -> list[str]:
    """整份计划总条目 5–40（阶段数 2–8 已在模型层）。"""
    total = sum(len(stage.items) for stage in draft.stages)
    if total < MIN_ITEMS:
        return ["条目总数 %d 少于下限 %d（每阶段至少 1 条）" % (total, MIN_ITEMS)]
    if total > MAX_ITEMS:
        return ["条目总数 %d 超过上限 %d" % (total, MAX_ITEMS)]
    return []


def check_duration(draft: PlanDraft, target_minutes: float) -> list[str]:
    """总估时与 `weeks × weekly_hours × 60` 偏差 >30% → 编排不自洽。"""
    if target_minutes <= 0:
        return []
    total = sum(item.est_minutes for stage in draft.stages for item in stage.items)
    deviation = abs(total - target_minutes) / target_minutes
    if deviation <= DURATION_TOLERANCE:
        return []
    return [
        "总估时 %d 分钟与目标 %d 分钟偏差 %.0f%%，超过 %d%%"
        % (total, int(target_minutes), deviation * 100, int(DURATION_TOLERANCE * 100))
    ]


def check_level_order(draft: PlanDraft, level_by_slug: dict[str, str]) -> list[str]:
    """难度不得倒挂：后面的阶段不得比前面更简单。

    按阶段取「阶段内最高难度」形成序列，要求非递减；`advanced` 出现在
    `beginner` 阶段之前（即序列下降）就报错。`note` 含「选学」的条目豁免。
    """
    errors: list[str] = []
    highest_rank = -1
    highest_stage = ""
    for stage_index, stage in enumerate(draft.stages, start=1):
        ranks = []
        for item in stage.items:
            if item.resource_slug is None or EXEMPT_MARK in item.note:
                continue
            level = level_by_slug.get(item.resource_slug)
            if level in LEVEL_RANK:
                ranks.append(LEVEL_RANK[level])
        if not ranks:
            continue
        stage_rank = max(ranks)
        if stage_rank > highest_rank:
            highest_rank = stage_rank
            highest_stage = stage.title
        elif stage_rank < highest_rank:
            errors.append(
                "第 %d 阶段「%s」的难度低于前面阶段「%s」：advanced 资料不得出现在"
                " beginner 阶段之前（如需保留请在该条目 note 标注「选学」）"
                % (stage_index, stage.title, highest_stage)
            )
    return errors
