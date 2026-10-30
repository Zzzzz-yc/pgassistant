/**
 * PlanItemRow（Owner：计划模块 M3，§9.4 业务组件表 / §9.1 条目行）。
 *
 * 一行里必须有的六件东西：勾选框、标题、类型图标、时长、绑定状态、拖拽手柄。
 *
 * 三态口径（§8.4）：`todo / done / skipped`。勾选框只管 done↔todo，
 * 「跳过」是独立按钮（把 skipped 塞进 Checkbox 的 indeterminate 会让读屏念不出状态）。
 *
 * 标题用 `resolved_title`（后端按 slug 实时查的最新标题），取不到才回退快照，
 * 并标灰 + 「资料已下架」（§8.4 最后一段）——不要在前端自己比对标题。
 *
 * 契约缺口（已写进报告 CONTRACT_IMPACT）：`ItemRead` 没有 `type` / `level` 字段，
 * 所以这里画不出「资料类型图标」与「难度徽章」。当前用「已绑资料 / 自定义任务」区分
 * （`FileText` / `PenLine`）；要真正显示类型与难度，需要 API 给条目补字段。
 */
import { Ban, FileText, GripVertical, PenLine, Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge, Button, Checkbox, Dialog, IconButton, Input, Textarea, cn } from "@/components/ui";
import { DurationLabel } from "@/components/business/DurationLabel";
import { TypeIcon } from "@/components/business/TypeIcon";
import { orderDirection } from "@/features/plans/dnd";
import type { ItemRead, PlanItemStatus } from "@/types/api";

export interface PlanItemRowProps {
  item: ItemRead;
  /** 只允许改时长与备注（§8.3 条目只列这几项）。 */
  onEdit: (changes: { est_minutes?: number; note?: string }) => void;
  onStatusChange: (status: PlanItemStatus) => void;
  onDelete: () => void;
  /** 已绑资料且资料存在时才可跳转（§8.5）。 */
  onOpen?: () => void;
  onDragStart: (event: React.DragEvent<HTMLButtonElement>) => void;
  /** 键盘兜底：手柄上按 ↑/↓ 等价于拖一格。 */
  onMoveByKeyboard?: (direction: -1 | 1) => void;
  /** 拖到这一行的上沿还是下沿（由父级算好，父级负责画插入线）。 */
  dropEdge?: "before" | "after" | null;
  isDragging?: boolean;
  busy?: boolean;
  onDragOver?: (event: React.DragEvent<HTMLLIElement>) => void;
  onDragLeave?: () => void;
  onDrop?: (event: React.DragEvent<HTMLLIElement>) => void;
  className?: string;
}

export function PlanItemRow({
  item,
  onEdit,
  onStatusChange,
  onDelete,
  onOpen,
  onDragStart,
  onMoveByKeyboard,
  dropEdge = null,
  isDragging = false,
  busy = false,
  onDragOver,
  onDragLeave,
  onDrop,
  className,
}: PlanItemRowProps) {
  const { t } = useTranslation("plans");
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const bound = item.resource_slug !== null;
  const unavailable = bound && !item.resource_available;
  const displayTitle = item.resolved_title ?? item.title;
  const openable = bound && item.resource_available && !!onOpen;
  const skipped = item.status === "skipped";

  return (
    <li
      className={cn(
        "plan-item",
        skipped && "plan-item--skipped",
        item.status === "done" && "plan-item--done",
        unavailable && "plan-item--unavailable",
        dropEdge === "before" && "is-drop-before",
        dropEdge === "after" && "is-drop-after",
        isDragging && "is-dragging",
        className,
      )}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <Checkbox
        checked={item.status === "done"}
        onCheckedChange={(checked) => onStatusChange(checked === true ? "done" : "todo")}
        disabled={busy}
        ariaLabel={item.status === "done" ? t("plan.item.todo") : t("plan.item.done")}
      />

      <span className="plan-item__type">
        {item.resource_type ? (
          // D25：类型图标来自 resources.type 的实时解析（article/video/course/…）
          <TypeIcon type={item.resource_type} size={14} />
        ) : (
          <span
            role="img"
            title={bound ? t("plan.item.bound") : t("plan.item.unbound")}
            aria-label={bound ? t("plan.item.bound") : t("plan.item.unbound")}
          >
            {bound ? (
              <FileText size={14} aria-hidden="true" />
            ) : (
              <PenLine size={14} aria-hidden="true" />
            )}
          </span>
        )}
      </span>

      <span className="plan-item__main">
        {openable ? (
          <button type="button" className="plan-item__title" onClick={onOpen}>
            {displayTitle}
          </button>
        ) : (
          <span className="plan-item__title plan-item__title--static">{displayTitle}</span>
        )}
        {/* D25：难度徽章；§9.3 规定 --accent 只用于进度强调与难度 */}
        {item.resource_level ? (
          <Badge tone="accent" className="plan-item__level">
            {t(`plan.level.${item.resource_level}`)}
          </Badge>
        ) : null}
        {unavailable ? (
          <Badge tone="neutral" className="plan-item__badge--muted">
            {t("plan.item.unavailable")}
          </Badge>
        ) : null}
        {!bound ? <Badge tone="neutral">{t("plan.item.custom")}</Badge> : null}
        {item.note ? <span className="plan-item__note">{item.note}</span> : null}
      </span>

      <DurationLabel minutes={item.est_minutes} className="plan-item__duration tnum" />

      <span className="plan-item__actions">
        <IconButton
          label={skipped ? t("plan.item.todo") : t("plan.item.skipped")}
          icon={<Ban size={14} />}
          size="sm"
          variant={skipped ? "secondary" : "ghost"}
          className={skipped ? "is-active" : undefined}
          onClick={() => onStatusChange(skipped ? "todo" : "skipped")}
          disabled={busy}
        />
        <IconButton
          label={t("plan.item.duration")}
          icon={<Pencil size={14} />}
          size="sm"
          onClick={() => setEditing(true)}
          disabled={busy}
        />
        <IconButton
          label={t("plan.item.delete")}
          icon={<Trash2 size={14} />}
          size="sm"
          onClick={() => setConfirming(true)}
          disabled={busy}
        />
        <button
          type="button"
          className="plan-item__handle"
          draggable={!busy}
          aria-label={t("plan.item.dragHint")}
          title={t("plan.item.dragHint")}
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

      <EditItemDialog
        open={editing}
        item={item}
        onClose={() => setEditing(false)}
        onSave={(changes) => {
          onEdit(changes);
          setEditing(false);
        }}
      />

      <Dialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t("plan.item.deleteTitle")}
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
              {t("plan.item.delete")}
            </Button>
          </div>
        }
      >
        <p className="plan-dialog__note">
          {t("plan.item.deleteBody", { title: displayTitle })}
        </p>
      </Dialog>
    </li>
  );
}

interface EditItemDialogProps {
  open: boolean;
  item: ItemRead;
  onClose: () => void;
  onSave: (changes: { est_minutes?: number; note?: string }) => void;
}

/** 时长 10–240、备注 0–200（API.md §3.14.5），越界在提交前就拦住。 */
function EditItemDialog({ open, item, onClose, onSave }: EditItemDialogProps) {
  const { t } = useTranslation("plans");
  const [minutes, setMinutes] = useState(String(item.est_minutes));
  const [note, setNote] = useState(item.note);

  useEffect(() => {
    if (!open) return;
    setMinutes(String(item.est_minutes));
    setNote(item.note);
  }, [open, item.est_minutes, item.note]);

  const parsed = Number.parseInt(minutes, 10);
  const minutesError =
    !Number.isFinite(parsed) || parsed < 10 || parsed > 240 ? t("new.invalid.duration") : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={t("plan.item.duration")}
      footer={
        <div className="plan-dialog__actions">
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={!!minutesError}
            onClick={() => onSave({ est_minutes: parsed, note: note.trim().slice(0, 200) })}
          >
            {t("common.save")}
          </Button>
        </div>
      }
    >
      <div className="plan-dialog__form">
        <Input
          label={t("plan.item.duration")}
          type="number"
          min={10}
          max={240}
          step={5}
          value={minutes}
          onChange={(event) => setMinutes(event.target.value)}
          error={minutesError}
        />
        <Textarea
          label={t("plan.item.note")}
          value={note}
          maxLength={200}
          rows={3}
          onChange={(event) => setNote(event.target.value)}
          counter={`${note.length}/200`}
        />
      </div>
    </Dialog>
  );
}
