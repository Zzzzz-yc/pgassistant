/**
 * 新建计划 —— `/plans/new`（Owner：计划模块 M3，§8.2 / §8.6 / §9.5）。
 *
 * 两步：**表单**（目标 + 基础 + 每周小时 + 周期 + 偏好 + 模板）→ **预览编辑页**。
 * 硬规则（§8.2 第 5 步）：**点「保存计划」之前一行都不写库**；离开预览页要弹确认。
 *
 * 三条生成路径（§8.6）：
 * - 套用模板：`GET /api/plan-templates/{id}` → 草稿直接进预览（0 token）；
 * - 从空白/表单搭骨架：本地 `draftFromForm`，条目自己加；
 * - 完全生成（AI）：`POST /api/agent/plan-draft` 属 M4。本页把「一句话抽取」按钮
 *   做成**禁用占位**并标注 M4 落地，不假装已经可用。
 */
import { ArrowLeft, ArrowRight, Plus, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Button, Card, Dialog, IconButton, Input, Skeleton, Tag, Textarea } from "@/components/ui";
import {
  cloneDraft,
  draftFromForm,
  useCreatePlan,
  usePlanTemplates,
} from "@/features/plans/hooks";
import { getPlanTemplate } from "@/lib/api/plans";
import { ApiError } from "@/lib/http";
import { useToast } from "@/lib/query";
import type { ItemDraft, Level, PlanDraft, PreferType, StageDraft } from "@/types/api";

import "@/features/plans/plans.css";

/** 常见目标快捷标签（§8.2：自由文本 + 常见岗位快捷标签）。 */
const GOAL_TAGS: { key: string; goal: string; templateId: string }[] = [
  { key: "frontend", goal: "三个月转前端，做完整项目", templateId: "frontend-entry" },
  { key: "backend", goal: "系统入门后端开发", templateId: "backend-entry" },
  { key: "algo", goal: "算法与数据结构基础", templateId: "algo-basics" },
  { key: "interview", goal: "面试冲刺，查漏补缺", templateId: "interview-sprint" },
];

interface FormState {
  goal: string;
  level: Level;
  weeklyHours: number;
  weeks: number;
  prefer: PreferType;
}

export default function NewPlanPage() {
  const { t } = useTranslation(["plans", "common"]);
  const navigate = useNavigate();
  const toastApi = useToast();
  const templates = usePlanTemplates();
  const createPlan = useCreatePlan();

  const [step, setStep] = useState<"form" | "preview">("form");
  const [form, setForm] = useState<FormState>({
    goal: "",
    level: "beginner",
    weeklyHours: 8,
    weeks: 12,
    prefer: "any",
  });
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [draft, setDraft] = useState<PlanDraft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [leaveOpen, setLeaveOpen] = useState(false);

  const updateDraft = (next: PlanDraft) => {
    setDraft(next);
    setDirty(true);
  };

  // 预览页的离开确认（§8.2 第 5 步）：浏览器级 + 页面内按钮级各一道
  useEffect(() => {
    if (!dirty || step !== "preview") return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, step]);

  const templateItems = templates.data?.items ?? [];

  const startPreview = async () => {
    setLoadingTemplate(true);
    try {
      if (templateId) {
        try {
          const templateDraft = await getPlanTemplate(templateId);
          setDraft(cloneDraft(templateDraft));
          setDirty(false);
          setStep("preview");
          return;
        } catch (error) {
          // 模板缺失 → 不报错，回退到表单骨架（API.md §3.15 最后一段）
          if (error instanceof ApiError && error.code === "PLAN_TEMPLATE_NOT_FOUND") {
            toastApi.push({ kind: "warn", message: t("new.template.failed") });
          } else {
            throw error;
          }
        }
      }
      setDraft(draftFromForm(form));
      setDirty(false);
      setStep("preview");
    } catch (error) {
      toastApi.push({
        kind: "error",
        message: error instanceof ApiError ? error.message : t("common:state.error"),
      });
    } finally {
      setLoadingTemplate(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    const problems = validate(draft);
    if (problems.length > 0) {
      toastApi.push({ kind: "warn", message: t("new.invalid.title"), hint: t("new.invalid.count") });
      return;
    }
    try {
      const plan = await createPlan.mutateAsync({
        draft: normalize(draft),
        origin: templateId ? "template" : "manual",
        template_id: templateId,
        start_level: form.level,
        set_active: true,
      });
      setDirty(false);
      navigate(`/plans/${plan.id}`);
    } catch {
      // useCreatePlan 已经按错误码弹过 Toast（含 422 PLAN_INVALID）
    }
  };

  const backToForm = () => {
    if (dirty) {
      setLeaveOpen(true);
      return;
    }
    setStep("form");
  };

  return (
    <section className="page page-enter plan-new">
      <header className="plan-new__head">
        <div>
          <h1 className="page__title">{t("new.title")}</h1>
          <ol className="plan-new__steps">
            <li className={step === "form" ? "is-current" : "is-done"}>{t("new.step.form")}</li>
            <li className={step === "preview" ? "is-current" : ""}>{t("new.step.preview")}</li>
          </ol>
        </div>
        <Button
          variant="ghost"
          iconLeft={<ArrowLeft size={16} aria-hidden="true" />}
          onClick={() => (step === "preview" ? backToForm() : navigate("/plans"))}
        >
          {t("plan.backToList")}
        </Button>
      </header>

      {step === "form" ? (
        <>
          <Card
            title={
              <span className="plan-new__section-title">
                <Sparkles size={16} aria-hidden="true" />
                {t("new.quick.title")}
              </span>
            }
          >
            <div className="plan-new__quick">
              <Input
                placeholder={t("new.quick.placeholder")}
                aria-label={t("new.quick.title")}
                value={form.goal}
                onChange={(event) => setForm({ ...form, goal: event.target.value })}
                maxLength={120}
              />
              <Button
                variant="secondary"
                disabled
                title={t("new.quick.disabled")}
                iconLeft={<Sparkles size={14} aria-hidden="true" />}
              >
                {t("new.quick.button")}
              </Button>
            </div>
            <p className="plan-new__hint">{t("new.quick.note")}</p>
          </Card>

          <Card>
            <Textarea
              label={t("new.goal.label")}
              hint={t("new.goal.hint")}
              placeholder={t("new.goal.placeholder")}
              value={form.goal}
              maxLength={120}
              rows={3}
              counter={`${form.goal.length}/120`}
              onChange={(event) => setForm({ ...form, goal: event.target.value })}
            />
            <div className="plan-new__tags" role="group" aria-label={t("new.quickTags.label")}>
              <span className="plan-new__tags-label">{t("new.quickTags.label")}</span>
              {GOAL_TAGS.map((tag) => (
                <Tag
                  key={tag.key}
                  selected={templateId === tag.templateId}
                  onClick={() => {
                    setForm({ ...form, goal: tag.goal });
                    setTemplateId(tag.templateId);
                  }}
                >
                  {t(`new.tag.${tag.key}`)}
                </Tag>
              ))}
            </div>
          </Card>

          <Card>
            <div className="plan-new__grid">
              <div className="plan-new__field">
                <span className="plan-new__label">{t("new.base.label")}</span>
                <div className="plan-new__radios" role="radiogroup" aria-label={t("new.base.label")}>
                  {(["beginner", "intermediate", "advanced"] as const).map((level) => (
                    <Tag
                      key={level}
                      selected={form.level === level}
                      onClick={() => setForm({ ...form, level })}
                    >
                      {t(`new.base.${level}`)}
                    </Tag>
                  ))}
                </div>
              </div>

              <Input
                label={t("new.hours.label")}
                type="number"
                min={1}
                max={40}
                value={String(form.weeklyHours)}
                onChange={(event) =>
                  setForm({ ...form, weeklyHours: Number.parseInt(event.target.value, 10) || 1 })
                }
                hint={t("new.hours.unit")}
              />

              <Input
                label={t("new.weeks.label")}
                type="number"
                min={4}
                max={52}
                value={String(form.weeks)}
                onChange={(event) =>
                  setForm({ ...form, weeks: Number.parseInt(event.target.value, 10) || 4 })
                }
                hint={t("new.weeks.unit")}
              />

              <div className="plan-new__field">
                <span className="plan-new__label">{t("new.prefer.label")}</span>
                <div className="plan-new__radios" role="radiogroup" aria-label={t("new.prefer.label")}>
                  {(["video", "docs", "any"] as const).map((prefer) => (
                    <Tag
                      key={prefer}
                      selected={form.prefer === prefer}
                      onClick={() => setForm({ ...form, prefer })}
                    >
                      {t(`new.prefer.${prefer}`)}
                    </Tag>
                  ))}
                </div>
              </div>
            </div>
          </Card>

          <Card title={t("new.template.title")}>
            <p className="plan-new__hint">{t("new.template.note")}</p>
            {templates.isLoading ? (
              <div className="plan-new__templates" aria-hidden="true">
                {[0, 1, 2, 3].map((index) => (
                  <Skeleton key={index} height={72} />
                ))}
              </div>
            ) : (
              <div className="plan-new__templates" role="radiogroup" aria-label={t("new.template.title")}>
                <button
                  type="button"
                  className={templateId === null ? "plan-template is-selected" : "plan-template"}
                  aria-pressed={templateId === null}
                  onClick={() => setTemplateId(null)}
                >
                  <span className="plan-template__title">{t("new.template.blank")}</span>
                  <span className="plan-template__meta">{t("new.template.blankNote")}</span>
                </button>
                {templateItems.map((template) => (
                  <button
                    key={template.template_id}
                    type="button"
                    className={
                      templateId === template.template_id
                        ? "plan-template is-selected"
                        : "plan-template"
                    }
                    aria-pressed={templateId === template.template_id}
                    onClick={() => setTemplateId(template.template_id)}
                  >
                    <span className="plan-template__title">
                      {template.label?.zh ?? template.title}
                    </span>
                    <span className="plan-template__meta">
                      {t("new.template.meta", {
                        stages: template.stage_count,
                        items: template.item_count,
                      })}
                    </span>
                    <span className="plan-template__goal">{template.goal}</span>
                  </button>
                ))}
              </div>
            )}
          </Card>

          <div className="plan-new__footer">
            <Button
              variant="primary"
              loading={loadingTemplate}
              iconRight={<ArrowRight size={16} aria-hidden="true" />}
              onClick={() => void startPreview()}
            >
              {t("new.submit")}
            </Button>
          </div>
        </>
      ) : draft ? (
        <PreviewEditor
          draft={draft}
          onChange={updateDraft}
          onBack={backToForm}
          onSave={() => void save()}
          saving={createPlan.isPending}
        />
      ) : null}

      <Dialog
        open={leaveOpen}
        onOpenChange={setLeaveOpen}
        title={t("new.leave.title")}
        footer={
          <div className="plan-dialog__actions">
            <Button variant="ghost" onClick={() => setLeaveOpen(false)}>
              {t("new.leave.cancel")}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setLeaveOpen(false);
                setDirty(false);
                setStep("form");
              }}
            >
              {t("new.leave.confirm")}
            </Button>
          </div>
        }
      >
        <p className="plan-dialog__note">{t("new.leave.body")}</p>
      </Dialog>
    </section>
  );
}

/* ------------------------------------------------------------ 预览编辑页 */

interface PreviewEditorProps {
  draft: PlanDraft;
  onChange: (draft: PlanDraft) => void;
  onBack: () => void;
  onSave: () => void;
  saving: boolean;
}

function PreviewEditor({ draft, onChange, onBack, onSave, saving }: PreviewEditorProps) {
  const { t } = useTranslation("plans");

  const totalItems = draft.stages.reduce((sum, stage) => sum + stage.items.length, 0);
  const totalMinutes = draft.stages.reduce(
    (sum, stage) =>
      sum + stage.items.reduce((stageSum, item) => stageSum + (item.est_minutes ?? 30), 0),
    0,
  );
  const problems = useMemo(() => validate(draft), [draft]);

  const patchStage = (index: number, changes: Partial<StageDraft>) => {
    onChange({
      ...draft,
      stages: draft.stages.map((stage, current) =>
        current === index ? { ...stage, ...changes } : stage,
      ),
    });
  };

  const patchItem = (stageIndex: number, itemIndex: number, changes: Partial<ItemDraft>) => {
    patchStage(stageIndex, {
      items: draft.stages[stageIndex].items.map((item, current) =>
        current === itemIndex ? { ...item, ...changes } : item,
      ),
    });
  };

  return (
    <div className="plan-preview">
      <p className="plan-preview__note">{t("new.preview.note")}</p>

      <div className="plan-preview__meta">
        <Input
          label={t("list.rename.label")}
          value={draft.title}
          maxLength={60}
          onChange={(event) => onChange({ ...draft, title: event.target.value })}
        />
        <Input
          label={t("new.goal.label")}
          value={draft.goal}
          maxLength={120}
          onChange={(event) => onChange({ ...draft, goal: event.target.value })}
        />
        <Input
          label={t("new.weeks.label")}
          type="number"
          min={4}
          max={52}
          value={String(draft.weeks)}
          onChange={(event) =>
            onChange({ ...draft, weeks: Number.parseInt(event.target.value, 10) || 4 })
          }
        />
        <Input
          label={t("new.hours.label")}
          type="number"
          min={1}
          max={40}
          value={String(draft.weekly_hours)}
          onChange={(event) =>
            onChange({ ...draft, weekly_hours: Number.parseInt(event.target.value, 10) || 1 })
          }
        />
      </div>

      <p className="plan-preview__total tnum">
        {t("new.preview.total", { items: totalItems, minutes: totalMinutes })}
      </p>

      <ol className="plan-preview__stages">
        {draft.stages.map((stage, stageIndex) => (
          <li className="plan-preview__stage" key={stageIndex}>
            <div className="plan-preview__stage-head">
              <span className="plan-preview__index tnum">{stageIndex + 1}</span>
              <Input
                aria-label={t("new.preview.stageTitle")}
                value={stage.title}
                maxLength={30}
                onChange={(event) => patchStage(stageIndex, { title: event.target.value })}
              />
              <Input
                aria-label={t("new.preview.stageWeeks")}
                type="number"
                min={1}
                max={52}
                value={String(stage.weeks)}
                fieldClassName="plan-preview__weeks"
                onChange={(event) =>
                  patchStage(stageIndex, {
                    weeks: Number.parseInt(event.target.value, 10) || 1,
                  })
                }
              />
              <IconButton
                label={t("new.preview.removeStage")}
                icon={<Trash2 size={14} />}
                onClick={() =>
                  onChange({
                    ...draft,
                    stages: draft.stages.filter((_, current) => current !== stageIndex),
                  })
                }
                disabled={draft.stages.length <= 2}
              />
            </div>

            <ul className="plan-preview__items">
              {stage.items.map((item, itemIndex) => (
                <li className="plan-preview__item" key={itemIndex}>
                  <Input
                    aria-label={t("new.preview.itemTitle")}
                    value={item.title}
                    maxLength={80}
                    onChange={(event) =>
                      patchItem(stageIndex, itemIndex, { title: event.target.value })
                    }
                  />
                  <Input
                    aria-label={t("new.preview.duration")}
                    type="number"
                    min={10}
                    max={240}
                    step={5}
                    value={String(item.est_minutes ?? 30)}
                    fieldClassName="plan-preview__minutes"
                    onChange={(event) =>
                      patchItem(stageIndex, itemIndex, {
                        est_minutes: Number.parseInt(event.target.value, 10) || 30,
                      })
                    }
                  />
                  <Input
                    aria-label={t("new.preview.noteField")}
                    value={item.note ?? ""}
                    maxLength={200}
                    placeholder={t("new.preview.noteField")}
                    onChange={(event) =>
                      patchItem(stageIndex, itemIndex, { note: event.target.value })
                    }
                  />
                  <IconButton
                    label={t("new.preview.removeItem")}
                    icon={<Trash2 size={14} />}
                    onClick={() =>
                      patchStage(stageIndex, {
                        items: stage.items.filter((_, current) => current !== itemIndex),
                      })
                    }
                    disabled={stage.items.length <= 1}
                  />
                </li>
              ))}
            </ul>

            <button
              type="button"
              className="plan-stage__add"
              onClick={() =>
                patchStage(stageIndex, {
                  items: [...stage.items, { title: t("new.preview.custom"), est_minutes: 30 }],
                })
              }
              disabled={stage.items.length >= 20}
            >
              <Plus size={14} aria-hidden="true" />
              {t("new.preview.addItem")}
            </button>
          </li>
        ))}
      </ol>

      <div className="plan-new__footer">
        <Button
          variant="ghost"
          onClick={() =>
            onChange({
              ...draft,
              stages: [
                ...draft.stages,
                {
                  title: "新阶段",
                  weeks: 1,
                  description: "",
                  items: [{ title: "自定义任务", est_minutes: 30 }],
                },
              ],
            })
          }
          disabled={draft.stages.length >= 8}
          iconLeft={<Plus size={14} aria-hidden="true" />}
        >
          {t("new.preview.addStage")}
        </Button>
        <span className="plan-preview__spacer" />
        <Button variant="ghost" onClick={onBack}>
          {t("new.step.form")}
        </Button>
        <Button
          variant="primary"
          loading={saving}
          loadingLabel={t("new.saving")}
          disabled={problems.length > 0}
          title={problems[0]}
          onClick={onSave}
        >
          {t("new.save")}
        </Button>
      </div>

      {problems.length > 0 ? (
        <div className="plan-preview__problems" role="status">
          <strong>{t("new.invalid.title")}</strong>
          <ul>
            {problems.slice(0, 4).map((problem) => (
              <li key={problem}>{t(`new.invalid.${problem}`)}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- 校验 */

/**
 * 与 API.md §3.10 的模型层约束保持一致（服务端仍会再校验一次，前端只为即时反馈）。
 * 返回的是**文案键**，不是句子——句子在 i18n 里，页面不拼中文。
 */
function validate(draft: PlanDraft): string[] {
  const problems: string[] = [];
  if (draft.title.trim().length < 1 || draft.title.length > 60) problems.push("titleField");
  if (draft.goal.trim().length < 1 || draft.goal.length > 120) problems.push("goalField");
  if (draft.weeks < 4 || draft.weeks > 52) problems.push("weeks");
  if (draft.weekly_hours < 1 || draft.weekly_hours > 40) problems.push("hours");
  if (draft.stages.length < 2 || draft.stages.length > 8) problems.push("count");
  const total = draft.stages.reduce((sum, stage) => sum + stage.items.length, 0);
  if (total < 5 || total > 40) problems.push("count");
  for (const stage of draft.stages) {
    if (stage.title.trim().length < 1 || stage.title.length > 30) problems.push("stageTitle");
    if (stage.items.length < 1 || stage.items.length > 20) problems.push("count");
    for (const item of stage.items) {
      if (item.title.trim().length < 1 || item.title.length > 80) problems.push("itemTitle");
      const minutes = item.est_minutes ?? 30;
      if (minutes < 10 || minutes > 240) problems.push("duration");
    }
  }
  return [...new Set(problems)];
}

/** 提交前裁掉行尾空白（标题 60/80 的上限按裁剪后的长度算才合理）。 */
function normalize(draft: PlanDraft): PlanDraft {
  return {
    ...draft,
    title: draft.title.trim(),
    goal: draft.goal.trim(),
    stages: draft.stages.map((stage) => ({
      ...stage,
      title: stage.title.trim(),
      items: stage.items.map((item) => ({
        ...item,
        title: item.title.trim(),
        note: (item.note ?? "").trim(),
        est_minutes: item.est_minutes ?? 30,
      })),
    })),
  };
}
