/**
 * TimelineStage（Owner：计划模块 M3，§9.4 业务组件表 / §8.5 纵向时间轴）。
 *
 * 「折叠头（序号 + 标题 + 进度小条 + 条目数）+ 条目列表」，左侧一条竖线 + 序号节点。
 * 阶段是**硬删除**且连带条目（D9 第 4 条），所以删除按钮必须二次确认并说清连带条数。
 *
 * 拖拽：阶段自己的手柄负责重排；阶段体（`<ul>`）同时是**条目跨阶段的落点**，
 * 两套 drop 目标都挂在组件的回调上，由页面判断 payload 的 `kind`。
 */
import { ChevronDown, ChevronRight, GripVertical, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState, type DragEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button, Dialog, IconButton, Input, ProgressBar } from "@/components/ui";
import { orderDirection } from "@/features/plans/dnd";
import type { StageRead } from "@/types/api";

export interface TimelineStageProps {
  stage: StageRead;
  /** 1 起的序号（显示用，不参与排序）。 */
  index: number;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onAddItem: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
  onDragStart: (event: DragEvent<HTMLButtonElement>) => void;
  onMoveByKeyboard?: (direction: -1 | 1) => void;
  isDragging?: boolean;
  dropEdge?: "before" | "after" | null;
  onDragOver?: (event: DragEvent<HTMLElement>) => void;
  onDragLeave?: () => void;
  onDrop?: (event: DragEvent<HTMLElement>) => void;
  /** 条目行（`<li>` 列表由页面传进来，含各自的 drop 处理）。 */
  children: ReactNode;
  /** 条目拖到阶段空白处 = 追加到该阶段末尾。 */
  onBodyDragOver?: (event: DragEvent<HTMLUListElement>) => void;
  onBodyDrop?: (event: DragEvent<HTMLUListElement>) => void;
  busy?: boolean;
}

export function TimelineStage({
  stage,
  index,
  collapsed,
  onToggleCollapsed,
  onAddItem,
  onRename,
  onDelete,
  onDragStart,
  onMoveByKeyboard,
  isDragging = false,
  dropEdge = null,
  onDragOver,
  onDragLeave,
  onDrop,
  children,
  onBodyDragOver,
  onBodyDrop,
  busy = false,
}: TimelineStageProps) {
  const { t } = useTranslation("plans");
  const [renaming, setRenaming] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const percent = stage.progress?.percent ?? 0;
  const itemCount = stage.items.length;

  return (
    <section
      className={[
        "plan-stage",
        collapsed && "is-collapsed",
        dropEdge === "before" ? "is-drop-before" : "",
        dropEdge === "after" ? "is-drop-after" : "",
        isDragging ? "is-dragging" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      aria-labelledby={`stage-title-${stage.id}`}
    >
      <div className="plan-stage__rail" aria-hidden="true">
        <span className="plan-stage__node tnum">{index}</span>
      </div>

      <div className="plan-stage__body">
        <div className="plan-stage__header">
          <button
            type="button"
            className="plan-stage__toggle"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-controls={`stage-items-${stage.id}`}
            aria-label={collapsed ? t("plan.stage.expand") : t("plan.stage.collapse")}
          >
            {collapsed ? (
              <ChevronRight size={16} aria-hidden="true" />
            ) : (
              <ChevronDown size={16} aria-hidden="true" />
            )}
          </button>

          <h2 className="plan-stage__title" id={`stage-title-${stage.id}`}>
            {stage.title}
          </h2>

          <ProgressBar value={percent} height={4} className="plan-stage__bar" />
          <span className="plan-stage__count tnum">
            {t("plan.stage.meta", { count: itemCount, percent: Math.round(percent) })}
          </span>

          <span className="plan-stage__actions">
            <IconButton
              label={t("plan.stage.addItem")}
              icon={<Plus size={14} />}
              size="sm"
              onClick={onAddItem}
              disabled={busy}
            />
            <IconButton
              label={t("plan.stage.rename")}
              icon={<Pencil size={14} />}
              size="sm"
              onClick={() => setRenaming(true)}
              disabled={busy}
            />
            <IconButton
              label={t("plan.stage.delete")}
              icon={<Trash2 size={14} />}
              size="sm"
              onClick={() => setConfirming(true)}
              disabled={busy}
            />
            <button
              type="button"
              className="plan-stage__handle"
              draggable={!busy}
              aria-label={t("plan.stage.orderHint")}
              title={t("plan.stage.orderHint")}
              onDragStart={onDragStart}
              onKeyDown={(event) => {
                const direction = orderDirection(event.key);
                if (!direction || !onMoveByKeyboard) return;
                event.preventDefault();
                onMoveByKeyboard(direction);
              }}
            >
              <GripVertical size={14} aria-hidden="true" />
            </button>
          </span>
        </div>

        {stage.description ? <p className="plan-stage__desc">{stage.description}</p> : null}

        <ul
          className="plan-stage__items"
          id={`stage-items-${stage.id}`}
          hidden={collapsed}
          onDragOver={onBodyDragOver}
          onDrop={onBodyDrop}
        >
          {children}
        </ul>

        {collapsed ? null : (
          <button type="button" className="plan-stage__add" onClick={onAddItem} disabled={busy}>
            <Plus size={14} aria-hidden="true" />
            {t("plan.stage.addItem")}
          </button>
        )}
      </div>

      <RenameStageDialog
        open={renaming}
        title={stage.title}
        onClose={() => setRenaming(false)}
        onSave={(next) => {
          onRename(next);
          setRenaming(false);
        }}
      />

      <Dialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("plan.stage.deleteTitle")}
        footer={
          <div className="plan-dialog__actions">
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setConfirming(false);
                onDelete();
              }}
            >
              {t("plan.stage.delete")}
            </Button>
          </div>
        }
      >
        <p className="plan-dialog__note">
          {t("plan.stage.deleteBody", { title: stage.title, count: itemCount })}
        </p>
      </Dialog>
    </section>
  );
}

interface RenameStageDialogProps {
  open: boolean;
  title: string;
  onClose: () => void;
  onSave: (title: string) => void;
}

function RenameStageDialog({ open, title, onClose, onSave }: RenameStageDialogProps) {
  const { t } = useTranslation("plans");
  const [value, setValue] = useState(title);

  useEffect(() => {
    if (!open) return;
    setValue(title);
  }, [open, title]);

  const trimmed = value.trim();
  const error = trimmed.length < 1 || trimmed.length > 30 ? t("new.invalid.stageTitle") : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={t("plan.stage.rename")}
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
        label={t("new.preview.stageTitle")}
        value={value}
        maxLength={30}
        onChange={(event) => setValue(event.target.value)}
        error={error}
      />
    </Dialog>
  );
}
