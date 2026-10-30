"""计划模板（`content/_meta/templates/*.yml`，需求文档 §8.6 / D10）。

模板是**数据不是代码**：随内容一起版本化，`template_id` = 文件名去扩展名。
YAML 顶层就是 §7.6 的 `plan_draft`，另允许两个展示用键 `label` / `summary`
（只进列表，不进 `PlanDraft`，因为模型是 `extra="forbid"`）。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from ..core import config
from ..schemas.models import (
    LocalizedLabel,
    PlanDraft,
    PlanTemplateSummary,
)
from . import plan_validate, yaml_subset

log = logging.getLogger("pgassistant.plan_templates")

TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: 模板比普通草稿更严（D10 质量硬要求，§8.6 明文）。
MIN_STAGES, MAX_STAGES = 4, 8
MIN_ITEMS_PER_STAGE, MAX_ITEMS_PER_STAGE = 3, 8


class TemplateNotFound(Exception):
    """模板文件不存在（→ 404 PLAN_TEMPLATE_NOT_FOUND）。"""

    def __init__(self, template_id: str) -> None:
        self.template_id = template_id
        super().__init__("template %s not found" % template_id)


def templates_dir() -> Path:
    """`content/_meta/templates`（相对项目根，随内容包一起走）。"""
    return config.SETTINGS.project_root / "content" / "_meta" / "templates"


def list_templates() -> list[PlanTemplateSummary]:
    """模板列表。单个模板坏了不拖垮列表：跳过并记 warning。"""
    items: list[PlanTemplateSummary] = []
    for path in sorted(templates_dir().glob("*.yml")):
        template_id = path.stem
        if not TEMPLATE_ID_RE.match(template_id):
            log.warning("模板文件名不合法，已跳过: %s", path.name)
            continue
        try:
            data = _read_yaml(path)
            draft = _draft_from_data(data, path)
            label = _label_from_data(data)
            items.append(
                PlanTemplateSummary(
                    template_id=template_id,
                    title=draft.title,
                    goal=draft.goal,
                    weeks=draft.weeks,
                    weekly_hours=draft.weekly_hours,
                    stage_count=len(draft.stages),
                    item_count=sum(len(stage.items) for stage in draft.stages),
                    label=label,
                )
            )
        except (ValueError, ValidationError, plan_validate.PlanInvalid) as exc:
            log.warning("模板 %s 解析失败，已跳过: %s", path.name, exc)
    return items


def load_template(template_id: str) -> PlanDraft:
    """读一份模板 → `PlanDraft`。结构/条目数/总时长走与 AI 草稿同一套校验。

    白名单不在这里查（`allowed_slugs is None` 即「信任模板」，MODULES §4.1），
    slug 存在性由 `validate_all()`（CI/脚本）负责，避免每次请求都读库。
    """
    if not TEMPLATE_ID_RE.match(template_id or ""):
        raise TemplateNotFound(template_id)
    path = templates_dir() / ("%s.yml" % template_id)
    if not path.is_file():
        raise TemplateNotFound(template_id)
    try:
        data = _read_yaml(path)
    except ValueError as exc:
        raise plan_validate.PlanInvalid(["%s: %s" % (path.name, exc)]) from exc
    draft = _draft_from_data(data, path)
    try:
        cleaned, _warnings = plan_validate.validate_draft(draft)
    except plan_validate.PlanInvalid as exc:
        raise plan_validate.PlanInvalid(
            ["%s: %s" % (path.name, error) for error in exc.errors]
        ) from exc
    return cleaned


def validate_all() -> list[str]:
    """CI / 脚本用：结构 + 模板质量 + slug 存在性 + 难度顺序；空列表 = 通过。

    返回的每条错误都带文件名，便于直接定位。
    """
    errors: list[str] = []
    paths = sorted(templates_dir().glob("*.yml"))
    if not paths:
        return ["模板目录为空或不存在: %s" % templates_dir()]

    slug_level = _resource_levels()
    if slug_level is None:
        errors.append("数据库不存在，无法校验模板引用的 slug 是否存在")

    for path in paths:
        template_id = path.stem
        if not TEMPLATE_ID_RE.match(template_id):
            errors.append("%s: 文件名必须匹配 ^[a-z0-9][a-z0-9-]*$" % path.name)
            continue
        try:
            draft = load_template(template_id)
        except (TemplateNotFound, plan_validate.PlanInvalid, ValueError,
                ValidationError) as exc:
            errors.append("%s: %s" % (path.name, exc))
            continue

        if not MIN_STAGES <= len(draft.stages) <= MAX_STAGES:
            errors.append(
                "%s: 阶段数 %d 不在 %d–%d 之间"
                % (path.name, len(draft.stages), MIN_STAGES, MAX_STAGES)
            )
        for stage_index, stage in enumerate(draft.stages, start=1):
            count = len(stage.items)
            if not MIN_ITEMS_PER_STAGE <= count <= MAX_ITEMS_PER_STAGE:
                errors.append(
                    "%s: 第 %d 阶段条目数 %d 不在 %d–%d 之间"
                    % (path.name, stage_index, count,
                       MIN_ITEMS_PER_STAGE, MAX_ITEMS_PER_STAGE)
                )

        if slug_level is None:
            continue
        referenced = {
            item.resource_slug
            for stage in draft.stages
            for item in stage.items
            if item.resource_slug
        }
        for slug in sorted(referenced - set(slug_level)):
            errors.append("%s: 引用了库内不存在的 slug: %s" % (path.name, slug))
        for message in plan_validate.check_level_order(draft, slug_level):
            errors.append("%s: %s" % (path.name, message))
    return errors


# ---------------------------------------------------------------- 内部工具
def _read_yaml(path: Path) -> dict:
    data = yaml_subset.load(path)
    if not isinstance(data, dict):
        raise yaml_subset.YamlSubsetError(1, "模板顶层必须是映射")
    return data


def _draft_from_data(data: dict, path: Path) -> PlanDraft:
    payload = dict(data)
    payload.pop("label", None)
    payload.pop("summary", None)
    try:
        return PlanDraft(**payload)
    except ValidationError as exc:
        details = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            details.append("%s: %s" % (location or "<root>", error["msg"]))
        raise plan_validate.PlanInvalid(
            ["%s: %s" % (path.name, detail) for detail in details]
        ) from exc


def _label_from_data(data: dict) -> LocalizedLabel | None:
    label = data.get("label")
    if not isinstance(label, dict):
        return None
    try:
        return LocalizedLabel(zh=label.get("zh"), en=label.get("en"))
    except ValidationError:
        return None


def _resource_levels() -> dict[str, str] | None:
    """只读打开当前库，返回 `slug -> level`；库不存在返回 None。"""
    db_path = Path(config.SETTINGS.db_path)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT slug, level FROM resources WHERE is_deleted = 0"
        ).fetchall()
        return {row[0]: row[1] for row in rows}
    except sqlite3.Error:
        return None
    finally:
        conn.close()
