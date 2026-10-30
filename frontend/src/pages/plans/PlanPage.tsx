/**
 * 计划详情（时间轴） —— `/plans/:planId`（Owner：计划模块 M3，§8.3 / §8.4 / §8.5 / §9.5）。
 *
 * 页面结构：**粘性头**（计划名 + 进度环 + 剩余周数 + 视图切换预留位）→ 时间轴（阶段折叠、
 * 序号、进度条、条目数）→ 桌面右侧「阶段大纲」。
 *
 * 交互契约（§8.3）：
 * - 全部写操作**乐观更新**（`usePlanMutations` 的 onMutate）+ 失败回滚；
 * - 409 用 `detail.current` 覆盖缓存并 Toast「已在别处更新，已为你刷新」，**不自动重放**；
 * - 拖拽排序 300ms 防抖后才提交（`useDebouncedCommit`），一次拖动不会发十几个请求。
 *
 * 条目标题点击 → `/plans/:planId/items/:slug` 分栏阅读（§9.1），左侧树由 AppShell 常驻，
 * 时间轴滚动位置存 sessionStorage，返回时还原。
 */
import { ListTree, Pencil, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import {
  Badge,
  Button,
  Dialog,
  IconButton,
  Input,
  ProgressRing,
  Skeleton,
  Tabs,
  Tag,
} from "@/components/ui";
import { PlanItemRow } from "@/features/plans/components/PlanItemRow";
import { TimelineStage } from "@/features/plans/components/TimelineStage";
import { dropEdge, middleOrderIndex, readDragPayload, writeDragPayload } from "@/features/plans/dnd";
import {
  useDebouncedCommit,
  usePlan,
  usePlanListActions,
  usePlanMutations,
} from "@/features/plans/hooks";
import { ApiError } from "@/lib/http";
import { useToast } from "@/lib/query";
import type { ItemRead, PlanItemStatus } from "@/types/api";

import "@/features/plans/plans.css";

const SCROLL_PREFIX = "plan-scroll:";

function scrollContainer(): HTMLElement | null {
  return document.querySelector<HTMLElement>(".app-main");
}

interface DropHint {
  kind: "stage" | "item";
  id: number;
  edge: "before" | "after";
}

export default function PlanPage() {
  const { planId: rawPlanId } = useParams();
  const planId = Number(rawPlanId);
  const valid = Number.isInteger(planId) && planId > 0;
  const { t } = useTranslation(["plans", "common"]);
  const navigate = useNavigate();
  const toastApi = useToast();

  const query = usePlan(valid ? planId : undefined);
  const plan = query.data;
  const mutations = usePlanMutations(valid ? planId : -1);
  const listActions = usePlanListActions();
  const debounce = useDebouncedCommit(300);

  const [onlyTodo, setOnlyTodo] = useState(false);
  const [onlyVideo, setOnlyVideo] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<number>>(() => new Set());
  const [dropHint, setDropHint] = useState<DropHint | null>(null);
  const [dragging, setDragging] = useState<{ kind: "stage" | "item"; id: number } | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [addingStage, setAddingStage] = useState(false);
  const [newStageTitle, setNewStageTitle] = useState("");
  const restoring = useRef(false);

  // 返回定位（§9.1「保持左侧上下文」的配套）：离开时记滚动位置，回来时还原
  useEffect(() => {
    if (!valid) return;
    const key = `${SCROLL_PREFIX}${planId}`;
    return () => {
      const container = scrollContainer();
      if (container) sessionStorage.setItem(key, String(container.scrollTop));
    };
  }, [planId, valid]);

  useEffect(() => {
    if (!plan || restoring.current) return;
    restoring.current = true;
    const saved = sessionStorage.getItem(`${SCROLL_PREFIX}${planId}`);
    if (!saved) return;
    const container = scrollContainer();
    if (!container) return;
    container.scrollTop = Number(saved) || 0;
  }, [plan, planId]);

  const toggleCollapsed = useCallback((stageId: number) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(stageId)) next.delete(stageId);
      else next.add(stageId);
      return next;
    });
  }, []);

  /**
   * 两个筛选条件**独立叠加**（§8.5「只看未完成 / 只看视频」）：
   * 「只看未完成」按条目状态，「只看视频」按 D25 的 `resource_type`。
   */
  const visibleItems = useCallback(
    (items: readonly ItemRead[]): ItemRead[] =>
      items.filter((item) => {
        if (onlyTodo && item.status !== "todo") return false;
        if (onlyVideo && item.resource_type !== "video") return false;
        return true;
      }),
    [onlyTodo, onlyVideo],
  );

  /* ------------------------------------------------------------- 拖拽 */

  const stageOrderFor = (targetId: number, edge: "before" | "after", movingId: number): number => {
    const rows = (plan?.stages ?? []).filter((stage) => stage.id !== movingId);
    const index = rows.findIndex((stage) => stage.id === targetId);
    if (index < 0) return (rows.at(-1)?.order_index ?? 0) + 1000;
    const before = edge === "before" ? rows[index - 1]?.order_index : rows[index]?.order_index;
    const after = edge === "before" ? rows[index]?.order_index : rows[index + 1]?.order_index;
    const middle = middleOrderIndex(before, after);
    if (middle !== null) return middle;
    return edge === "before" ? Math.max(0, (after ?? 0) - 1) : (before ?? 0) + 1;
  };

  const itemOrderFor = (
    stageId: number,
    targetId: number,
    edge: "before" | "after",
    movingId: number,
  ): number => {
    const stage = plan?.stages.find((entry) => entry.id === stageId);
    const rows = (stage?.items ?? []).filter((item) => item.id !== movingId);
    const index = rows.findIndex((item) => item.id === targetId);
    if (index < 0) return (rows.at(-1)?.order_index ?? 0) + 1000;
    const before = edge === "before" ? rows[index - 1]?.order_index : rows[index]?.order_index;
    const after = edge === "before" ? rows[index]?.order_index : rows[index + 1]?.order_index;
    const middle = middleOrderIndex(before, after);
    if (middle !== null) return middle;
    return edge === "before" ? Math.max(0, (after ?? 0) - 1) : (before ?? 0) + 1;
  };

  const handleStageDrop = (event: DragEvent<HTMLElement>, targetId: number) => {
    event.preventDefault();
    const payload = readDragPayload(event);
    const hint = dropHint;
    setDropHint(null);
    setDragging(null);
    if (!payload || payload.kind !== "stage" || !hint || hint.id !== targetId) return;
    if (payload.id === targetId) return;
    const orderIndex = stageOrderFor(targetId, hint.edge, payload.id);
    // 300ms 防抖：同一阶段连续被拖时只提交最后一次（§8.3 最后一条）
    debounce.schedule(`stage:${payload.id}`, () => {
      mutations.moveStage.mutate({ stageId: payload.id, orderIndex });
    });
  };

  const handleItemDrop = (event: DragEvent<HTMLElement>, stageId: number, targetId: number) => {
    event.preventDefault();
    event.stopPropagation();
    const payload = readDragPayload(event);
    const hint = dropHint;
    setDropHint(null);
    setDragging(null);
    if (!payload || payload.kind !== "item" || !hint || hint.id !== targetId) return;
    if (payload.id === targetId) return;
    const orderIndex = itemOrderFor(stageId, targetId, hint.edge, payload.id);
    debounce.schedule(`item:${payload.id}`, () => {
      mutations.patchItem.mutate({
        itemId: payload.id,
        patch: { stage_id: stageId, order_index: orderIndex },
      });
    });
  };

  /** 条目拖到阶段空白处 = 追加到该阶段末尾（跨阶段拖拽的常见落点）。 */
  const handleStageBodyDrop = (event: DragEvent<HTMLUListElement>, stageId: number) => {
    event.preventDefault();
    const payload = readDragPayload(event);
    setDropHint(null);
    setDragging(null);
    if (!payload || payload.kind !== "item") return;
    const stage = plan?.stages.find((entry) => entry.id === stageId);
    if (!stage) return;
    const last = stage.items.filter((item) => item.id !== payload.id).at(-1);
    const orderIndex = (last?.order_index ?? 0) + 1000;
    debounce.schedule(`item:${payload.id}`, () => {
      mutations.patchItem.mutate({
        itemId: payload.id,
        patch: { stage_id: stageId, order_index: orderIndex },
      });
    });
  };

  /* -------------------------------------------------------- 写操作封装 */

  const notify = (message: string) => toastApi.push({ kind: "success", message });

  const changeStatus = (item: ItemRead, status: PlanItemStatus) => {
    mutations.patchItem.mutate({ itemId: item.id, patch: { status } });
  };

  /* ------------------------------------------------------------ 渲染 */

  if (!valid) {
    return (
      <section className="page page-enter plan-page">
        <ErrorState
          code="PLAN_NOT_FOUND"
          onRetry={() => navigate("/plans")}
          retryLabel={t("plan.backToList")}
        />
      </section>
    );
  }

  if (query.isLoading) {
    return (
      <section className="page page-enter plan-page" aria-hidden="true">
        <Skeleton width="40%" height={28} />
        <Skeleton width="100%" height={120} />
        <Skeleton width="100%" height={220} />
      </section>
    );
  }

  if (query.isError || !plan) {
    const code = query.error instanceof ApiError ? query.error.code : "INTERNAL";
    return (
      <section className="page page-enter plan-page">
        <ErrorState code={code} detail={query.error} onRetry={() => void query.refetch()} />
        <div className="plan-page__foot">
          <Button variant="ghost" onClick={() => navigate("/plans")}>
            {t("plan.backToList")}
          </Button>
        </div>
      </section>
    );
  }

  const progress = plan.progress;
  const percent = progress?.percent ?? 0;
  const remaining = progress?.remaining_weeks ?? plan.horizon_weeks;
  // 筛完一条都不剩时给空态（不是空白时间轴）
  const visibleTotal = plan.stages.reduce(
    (total, stage) => total + visibleItems(stage.items).length,
    0,
  );
  const filtering = onlyTodo || onlyVideo;

  return (
    <section className="page page-enter plan-page">
      <header className="plan-head">
        <div className="plan-head__main">
          <div className="plan-head__title-row">
            <h1 className="plan-head__title">{plan.title}</h1>
            <IconButton
              label={t("list.action.rename")}
              icon={<Pencil size={15} />}
              size="sm"
              onClick={() => setRenaming(true)}
            />
            <Badge tone={plan.status === "active" ? "primary" : "neutral"}>
              {plan.status === "active" ? t("list.card.active") : t("list.card.archived")}
            </Badge>
          </div>
          {plan.goal ? <p className="plan-head__goal">{plan.goal}</p> : null}
          <p className="plan-head__meta tnum">
            {t("list.card.weeks", { weeks: plan.horizon_weeks, hours: plan.weekly_hours })}
            <span className="plan-head__dot" aria-hidden="true" />
            {t("plan.header.invested", {
              duration: `${Math.round((progress?.invested_minutes ?? 0) / 60)} h`,
            })}
          </p>
        </div>

        <div className="plan-head__progress">
          <ProgressRing
            percent={percent}
            size="lg"
            ariaLabel={t("plan.header.progress", { percent: Math.round(percent) })}
          />
          <div className="plan-head__remaining">
            <span className="tnum">{t("plan.header.remaining", { weeks: remaining })}</span>
            <span className="tnum plan-head__done">
              {t("summary.items", {
                done: progress?.done_items ?? 0,
                total:
                  (progress?.done_items ?? 0) +
                  (progress?.todo_items ?? 0) +
                  (progress?.skipped_items ?? 0),
              })}
            </span>
          </div>
        </div>

        <div className="plan-head__actions">
          <Button
            variant="ghost"
            size="sm"
            iconLeft={<ListTree size={14} aria-hidden="true" />}
            onClick={() => navigate("/plans")}
          >
            {t("plan.backToList")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              if (plan.status === "active") {
                mutations.setStatus.mutate("archived");
                notify(t("toast.archived"));
              } else {
                mutations.setStatus.mutate("active");
                notify(t("toast.activated"));
              }
            }}
          >
            {plan.status === "active" ? t("list.action.archive") : t("list.action.activate")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            iconLeft={<Trash2 size={14} aria-hidden="true" />}
            onClick={() => setDeleting(true)}
          >
            {t("list.action.delete")}
          </Button>
        </div>
      </header>

      {/* 视图切换预留位（§8.5：一期时间轴，二期节点图；同一份数据不重新取数） */}
      <Tabs
        className="plan-view"
        defaultValue="timeline"
        ariaLabel={t("plan.header.view.timeline")}
        items={[
          {
            value: "timeline",
            label: t("plan.header.view.timeline"),
            content: <span className="plan-view__hint" />,
          },
          {
            value: "graph",
            label: t("plan.header.view.graph"),
            content: <p className="plan-view__hint">{t("plan.header.view.graphHint")}</p>,
          },
        ]}
      />

      <div className="plan-layout">
        <div className="plan-layout__main">
          <div className="plan-filters" role="group" aria-label={t("plan.filter.label")}>
            <Tag
              selected={!onlyTodo && !onlyVideo}
              onClick={() => {
                setOnlyTodo(false);
                setOnlyVideo(false);
              }}
            >
              {t("plan.filter.all")}
            </Tag>
            <Tag selected={onlyTodo} onClick={() => setOnlyTodo((value) => !value)}>
              {t("plan.filter.todo")}
            </Tag>
            <Tag selected={onlyVideo} onClick={() => setOnlyVideo((value) => !value)}>
              {t("plan.filter.video")}
            </Tag>
          </div>

          {plan.stages.length === 0 ? (
            <EmptyState
              title={t("plan.outline.empty")}
              description={t("plan.stage.addItem")}
              action={
                <Button variant="primary" onClick={() => setAddingStage(true)}>
                  {t("plan.addStage")}
                </Button>
              }
            />
          ) : filtering && visibleTotal === 0 ? (
            <EmptyState
              title={t("plan.filter.empty.title")}
              description={t("plan.filter.empty.description")}
              action={
                <Button
                  variant="primary"
                  onClick={() => {
                    setOnlyTodo(false);
                    setOnlyVideo(false);
                  }}
                >
                  {t("plan.filter.empty.clear")}
                </Button>
              }
            />
          ) : (
            <div className="plan-timeline">
              {plan.stages.map((stage, index) => {
                const items = visibleItems(stage.items);
                return (
                  <TimelineStage
                    key={stage.id}
                    stage={stage}
                    index={index + 1}
                    collapsed={collapsed.has(stage.id)}
                    onToggleCollapsed={() => toggleCollapsed(stage.id)}
                    onAddItem={() =>
                      mutations.addItem.mutate({
                        stageId: stage.id,
                        title: t("plan.addItemTitle"),
                      })
                    }
                    onRename={(title) => {
                      mutations.renameStage.mutate({ stageId: stage.id, title });
                      notify(t("toast.stageRenamed"));
                    }}
                    onDelete={() => {
                      mutations.removeStage.mutate(stage.id);
                      notify(t("toast.stageDeleted"));
                    }}
                    onDragStart={(event) => {
                      writeDragPayload(event, { kind: "stage", id: stage.id });
                      setDragging({ kind: "stage", id: stage.id });
                    }}
                    onMoveByKeyboard={(direction) => {
                      const rows = plan.stages;
                      const current = rows.findIndex((entry) => entry.id === stage.id);
                      const neighbour = rows[current + direction];
                      if (!neighbour) return;
                      const orderIndex =
                        direction === -1
                          ? stageOrderFor(neighbour.id, "before", stage.id)
                          : stageOrderFor(neighbour.id, "after", stage.id);
                      mutations.moveStage.mutate({ stageId: stage.id, orderIndex });
                    }}
                    isDragging={dragging?.kind === "stage" && dragging.id === stage.id}
                    dropEdge={
                      dropHint?.kind === "stage" && dropHint.id === stage.id ? dropHint.edge : null
                    }
                    onDragOver={(event) => {
                      const payload = readDragPayload(event);
                      if (!payload || payload.kind !== "stage" || payload.id === stage.id) return;
                      event.preventDefault();
                      setDropHint({ kind: "stage", id: stage.id, edge: dropEdge(event) });
                    }}
                    onDragLeave={() => setDropHint(null)}
                    onDrop={(event) => handleStageDrop(event, stage.id)}
                    busy={mutations.removeStage.isPending}
                    onBodyDragOver={(event) => {
                      const payload = readDragPayload(event);
                      if (!payload || payload.kind !== "item") return;
                      event.preventDefault();
                    }}
                    onBodyDrop={(event) => handleStageBodyDrop(event, stage.id)}
                  >
                    {items.length === 0 ? (
                      <li className="plan-item plan-item--empty">
                        <span>{t("common:state.empty")}</span>
                      </li>
                    ) : (
                      items.map((item) => (
                        <PlanItemRow
                          key={item.id}
                          item={item}
                          busy={
                            mutations.patchItem.isPending &&
                            mutations.patchItem.variables?.itemId === item.id
                          }
                          onStatusChange={(status) => changeStatus(item, status)}
                          onEdit={(changes) => {
                            mutations.patchItem.mutate({ itemId: item.id, patch: changes });
                            notify(t("toast.itemUpdated"));
                          }}
                          onDelete={() => {
                            mutations.removeItem.mutate({
                              itemId: item.id,
                              version: item.version,
                            });
                            notify(t("toast.itemDeleted"));
                          }}
                          onOpen={
                            item.resource_slug && item.resource_available
                              ? () => navigate(`/plans/${planId}/items/${item.resource_slug}`)
                              : undefined
                          }
                          onDragStart={(event) => {
                            writeDragPayload(event, {
                              kind: "item",
                              id: item.id,
                              fromStageId: stage.id,
                            });
                            setDragging({ kind: "item", id: item.id });
                          }}
                          onMoveByKeyboard={(direction) => {
                            const rows = stage.items;
                            const current = rows.findIndex((entry) => entry.id === item.id);
                            const neighbour = rows[current + direction];
                            if (!neighbour) return;
                            const orderIndex =
                              direction === -1
                                ? itemOrderFor(stage.id, neighbour.id, "before", item.id)
                                : itemOrderFor(stage.id, neighbour.id, "after", item.id);
                            mutations.patchItem.mutate({
                              itemId: item.id,
                              patch: { order_index: orderIndex },
                            });
                          }}
                          isDragging={dragging?.kind === "item" && dragging.id === item.id}
                          dropEdge={
                            dropHint?.kind === "item" && dropHint.id === item.id
                              ? dropHint.edge
                              : null
                          }
                          onDragOver={(event) => {
                            const payload = readDragPayload(event);
                            if (!payload || payload.kind !== "item" || payload.id === item.id) {
                              return;
                            }
                            event.preventDefault();
                            event.stopPropagation();
                            setDropHint({ kind: "item", id: item.id, edge: dropEdge(event) });
                          }}
                          onDragLeave={() => setDropHint(null)}
                          onDrop={(event) => handleItemDrop(event, stage.id, item.id)}
                        />
                      ))
                    )}
                  </TimelineStage>
                );
              })}
            </div>
          )}

          <div className="plan-add-stage">
            {addingStage ? (
              <form
                className="plan-add-stage__form"
                onSubmit={(event) => {
                  event.preventDefault();
                  const title = newStageTitle.trim();
                  if (title.length < 1 || title.length > 30) return;
                  mutations.addStage.mutate({ title });
                  setNewStageTitle("");
                  setAddingStage(false);
                  notify(t("toast.stageAdded"));
                }}
              >
                <Input
                  aria-label={t("plan.addStagePlaceholder")}
                  placeholder={t("plan.addStagePlaceholder")}
                  value={newStageTitle}
                  maxLength={30}
                  autoFocus
                  onChange={(event) => setNewStageTitle(event.target.value)}
                />
                <Button type="submit" variant="primary">
                  {t("plan.addStage")}
                </Button>
                <Button variant="ghost" onClick={() => setAddingStage(false)}>
                  {t("common.cancel")}
                </Button>
              </form>
            ) : (
              <Button
                variant="secondary"
                iconLeft={<Plus size={16} aria-hidden="true" />}
                onClick={() => setAddingStage(true)}
              >
                {t("plan.addStage")}
              </Button>
            )}
          </div>
        </div>

        <aside className="plan-outline" aria-label={t("plan.outline.title")}>
          <h2 className="plan-outline__title">{t("plan.outline.title")}</h2>
          {plan.stages.length === 0 ? (
            <p className="plan-outline__empty">{t("plan.outline.empty")}</p>
          ) : (
            <ol className="plan-outline__list">
              {plan.stages.map((stage, index) => (
                <li key={stage.id}>
                  <button
                    type="button"
                    className="plan-outline__item"
                    onClick={() => {
                      document
                        .getElementById(`stage-title-${stage.id}`)
                        ?.scrollIntoView({ behavior: "smooth", block: "start" });
                    }}
                  >
                    <span className="tnum plan-outline__index">{index + 1}</span>
                    <span className="plan-outline__label">{stage.title}</span>
                    <span className="tnum plan-outline__percent">
                      {Math.round(stage.progress?.percent ?? 0)}%
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </aside>
      </div>

      <RenamePlanDialog
        open={renaming}
        title={plan.title}
        onClose={() => setRenaming(false)}
        onSave={(title) => {
          mutations.rename.mutate(title);
          setRenaming(false);
          notify(t("toast.renamed"));
        }}
      />

      <Dialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t("list.delete.title")}
        footer={
          <div className="plan-dialog__actions">
            <Button variant="ghost" onClick={() => setDeleting(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setDeleting(false);
                void (async () => {
                  try {
                    await listActions.remove.mutateAsync({
                      planId: plan.id,
                      version: plan.version,
                    });
                    notify(t("toast.deleted"));
                    navigate("/plans");
                  } catch {
                    // 失败提示由 usePlanListActions 的统一错误处理发出
                  }
                })();
              }}
            >
              {t("list.delete.confirm")}
            </Button>
          </div>
        }
      >
        <p className="plan-dialog__note">{t("list.delete.body", { title: plan.title })}</p>
      </Dialog>
    </section>
  );
}

interface RenamePlanDialogProps {
  open: boolean;
  title: string;
  onClose: () => void;
  onSave: (title: string) => void;
}

function RenamePlanDialog({ open, title, onClose, onSave }: RenamePlanDialogProps) {
  const { t } = useTranslation("plans");
  const [value, setValue] = useState(title);

  useEffect(() => {
    if (open) setValue(title);
  }, [open, title]);

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
          <Button variant="primary" disabled={!!error} onClick={() => onSave(trimmed)}>
            {t("common.save")}
          </Button>
        </div>
      }
    >
      <Input
        label={t("list.rename.label")}
        value={value}
        maxLength={60}
        onChange={(event) => setValue(event.target.value)}
        error={error}
        autoFocus
      />
    </Dialog>
  );
}
