/**
 * 计划列表 —— `/plans`（Owner：计划模块 M3，§8.1 / §9.5「计划列表」）。
 *
 * 一屏交代四件事：哪些计划在、哪个是 active、每份的进度、能对它们做什么。
 * 归档与软删除是**两件正交的事**（D9 第 5 条）：归档只是"不激活但仍存在"，
 * 删除是"什么都不在"，所以删掉的确认文案必须写清"本期没有撤销入口"。
 */
import { Archive, ArchiveRestore, Copy, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import { Badge, Button, Dialog, DropdownMenu, IconButton, Input, Skeleton, Tag } from "@/components/ui";
import { ProgressSummary } from "@/features/plans/components/ProgressSummary";
import { usePlanListActions, usePlans } from "@/features/plans/hooks";
import { getPlan } from "@/lib/api/plans";
import { ApiError } from "@/lib/http";
import { useToast } from "@/lib/query";
import type { PlanStatusFilter, PlanSummary } from "@/types/api";

import "@/features/plans/plans.css";

export default function PlansPage() {
  const { t } = useTranslation(["plans", "common"]);
  const navigate = useNavigate();
  const toastApi = useToast();
  const [filter, setFilter] = useState<PlanStatusFilter>("all");
  const [renaming, setRenaming] = useState<PlanSummary | null>(null);
  const [deleting, setDeleting] = useState<PlanSummary | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const { data, isLoading, isError, error, refetch } = usePlans(
    filter === "all" ? {} : { status: filter },
  );
  const actions = usePlanListActions();

  const items = data?.items ?? [];
  const activeId = data?.active_plan_id ?? null;

  const handleStatus = async (plan: PlanSummary, status: "active" | "archived") => {
    setBusyId(plan.id);
    try {
      await actions.setStatus.mutateAsync({ planId: plan.id, status });
      toastApi.push({
        kind: "success",
        message: status === "active" ? t("toast.activated") : t("toast.archived"),
      });
    } catch {
      // 失败提示由 usePlanListActions 的统一错误处理发出
    } finally {
      setBusyId(null);
    }
  };

  const handleDuplicate = async (plan: PlanSummary) => {
    setBusyId(plan.id);
    try {
      const full = await getPlan(plan.id);
      await actions.duplicate.mutateAsync(full);
      toastApi.push({ kind: "success", message: t("toast.duplicated") });
    } catch {
      // 同上
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (plan: PlanSummary) => {
    setDeleting(null);
    setBusyId(plan.id);
    try {
      await actions.remove.mutateAsync({ planId: plan.id, version: plan.version });
      toastApi.push({ kind: "success", message: t("toast.deleted") });
    } catch {
      // 同上
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="page page-enter plan-list">
      <header className="plan-list__head">
        <div>
          <h1 className="page__title">{t("list.title")}</h1>
          <p className="page__status">
            {t("list.subtitle", { count: data?.total ?? 0 })}
          </p>
        </div>
        <Button
          variant="primary"
          iconLeft={<Plus size={16} aria-hidden="true" />}
          onClick={() => navigate("/plans/new")}
        >
          {t("list.new")}
        </Button>
      </header>

      <div className="plan-list__filters" role="group" aria-label={t("plan.filter.label")}>
        {(["all", "active", "archived"] as const).map((key) => (
          <Tag
            key={key}
            selected={filter === key}
            onClick={() => setFilter(key)}
            size="md"
          >
            {t(`list.filter.${key}`)}
          </Tag>
        ))}
      </div>

      {isLoading ? (
        <div className="plan-list__grid" aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <div className="plan-card plan-card--skeleton" key={index}>
              <Skeleton width="60%" height={20} />
              <Skeleton width="90%" height={14} />
              <Skeleton shape="circle" width={52} height={52} />
            </div>
          ))}
        </div>
      ) : isError ? (
        <ErrorState
          code={error instanceof ApiError ? error.code : "INTERNAL"}
          detail={error}
          onRetry={() => void refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title={t("list.empty.title")}
          description={t("list.empty.description")}
          action={
            <Button variant="primary" onClick={() => navigate("/plans/new")}>
              {t("list.empty.action")}
            </Button>
          }
        />
      ) : (
        <div className="plan-list__grid">
          {items.map((plan) => (
            <article
              key={plan.id}
              className={plan.id === activeId ? "plan-card is-active" : "plan-card"}
            >
              <div className="plan-card__head">
                <Link className="plan-card__title" to={`/plans/${plan.id}`}>
                  {plan.title}
                </Link>
                <Badge tone={plan.status === "active" ? "primary" : "neutral"}>
                  {plan.status === "active" ? t("list.card.active") : t("list.card.archived")}
                </Badge>
                <DropdownMenu
                  align="end"
                  ariaLabel={plan.title}
                  trigger={
                    <IconButton
                      label={plan.title}
                      icon={<MoreHorizontal size={16} />}
                      size="sm"
                      disabled={busyId === plan.id}
                    />
                  }
                  entries={[
                    {
                      kind: "item",
                      key: "rename",
                      label: t("list.action.rename"),
                      icon: <Pencil size={14} />,
                      onSelect: () => setRenaming(plan),
                    },
                    plan.status === "active"
                      ? {
                          kind: "item" as const,
                          key: "archive",
                          label: t("list.action.archive"),
                          icon: <Archive size={14} />,
                          onSelect: () => void handleStatus(plan, "archived"),
                        }
                      : {
                          kind: "item" as const,
                          key: "activate",
                          label: t("list.action.activate"),
                          icon: <ArchiveRestore size={14} />,
                          onSelect: () => void handleStatus(plan, "active"),
                        },
                    {
                      kind: "item",
                      key: "duplicate",
                      label: t("list.action.duplicate"),
                      icon: <Copy size={14} />,
                      onSelect: () => void handleDuplicate(plan),
                    },
                    { kind: "separator", key: "sep" },
                    {
                      kind: "item",
                      key: "delete",
                      label: t("list.action.delete"),
                      icon: <Trash2 size={14} />,
                      danger: true,
                      onSelect: () => setDeleting(plan),
                    },
                  ]}
                />
              </div>

              {plan.goal ? <p className="plan-card__goal">{plan.goal}</p> : null}

              <Link className="plan-card__body" to={`/plans/${plan.id}`}>
                <ProgressSummary progress={plan.progress} size="md" compact />
              </Link>

              <footer className="plan-card__foot">
                <span className="tnum">
                  {t("list.card.weeks", { weeks: plan.horizon_weeks, hours: plan.weekly_hours })}
                </span>
                {plan.updated_at ? (
                  <span className="tnum">
                    {t("list.card.updated", {
                      time: new Date(plan.updated_at).toLocaleDateString(),
                    })}
                  </span>
                ) : null}
              </footer>
            </article>
          ))}
        </div>
      )}

      <RenamePlanDialog
        plan={renaming}
        onClose={() => setRenaming(null)}
        onSave={async (plan, title) => {
          setRenaming(null);
          try {
            await actions.rename.mutateAsync({ planId: plan.id, title });
            toastApi.push({ kind: "success", message: t("toast.renamed") });
          } catch {
            // 统一错误处理
          }
        }}
      />

      <Dialog
        open={deleting !== null}
        onOpenChange={(open) => (open ? undefined : setDeleting(null))}
        title={t("list.delete.title")}
        footer={
          <div className="plan-dialog__actions">
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="danger"
              onClick={() => deleting && void handleDelete(deleting)}
            >
              {t("list.delete.confirm")}
            </Button>
          </div>
        }
      >
        <p className="plan-dialog__note">
          {t("list.delete.body", { title: deleting?.title ?? "" })}
        </p>
      </Dialog>
    </section>
  );
}

interface RenamePlanDialogProps {
  plan: PlanSummary | null;
  onClose: () => void;
  onSave: (plan: PlanSummary, title: string) => void;
}

function RenamePlanDialog({ plan, onClose, onSave }: RenamePlanDialogProps) {
  const { t } = useTranslation("plans");
  const [value, setValue] = useState("");

  // 每次打开都用当前标题预填（Dialog 受控，自身不保存草稿）
  useEffect(() => {
    if (plan) setValue(plan.title);
    else setValue("");
  }, [plan]);

  const open = plan !== null;
  const trimmed = value.trim();
  const error = trimmed.length < 1 || trimmed.length > 60 ? t("new.invalid.titleField") : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={t("list.rename.title")}
      footer={
        <div className="plan-dialog__actions">
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={!!error || !plan}
            onClick={() => plan && onSave(plan, trimmed)}
          >
            {t("common.save")}
          </Button>
        </div>
      }
    >
      <Input
        label={t("list.rename.label")}
        placeholder={t("list.rename.placeholder")}
        value={value}
        maxLength={60}
        onChange={(event) => setValue(event.target.value)}
        error={error}
        autoFocus
      />
    </Dialog>
  );
}
