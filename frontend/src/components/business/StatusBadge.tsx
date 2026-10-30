/**
 * StatusBadge（业务组件）：计划条目三态、计划状态、索引状态、资料来源的统一徽章。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——状态是只读事实，
 * 不可点；"正在运行"由 `running` 这一**值**表达（accent 色 + 转圈图标），
 * 而不是组件的 loading 态。
 */
import { Archive, Check, Circle, CircleDot, Minus, Play, TriangleAlert } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui";

export type StatusKind =
  | "todo"
  | "done"
  | "skipped"
  | "active"
  | "archived"
  | "running"
  | "idle"
  | "failed";

interface StatusMeta {
  label: string;
  tone: BadgeTone;
  Icon: typeof Check;
}

const STATUS_META: Record<StatusKind, StatusMeta> = {
  todo: { label: "待学", tone: "neutral", Icon: Circle },
  done: { label: "完成", tone: "primary", Icon: Check },
  skipped: { label: "跳过", tone: "neutral", Icon: Minus },
  active: { label: "进行中", tone: "primary", Icon: CircleDot },
  archived: { label: "已归档", tone: "neutral", Icon: Archive },
  running: { label: "建立中", tone: "accent", Icon: Play },
  idle: { label: "空闲", tone: "neutral", Icon: Circle },
  failed: { label: "失败", tone: "danger", Icon: TriangleAlert },
};

export interface StatusBadgeProps {
  status: StatusKind;
  /** 覆盖文案（如"已完成 12/20"）。 */
  label?: string;
  size?: "sm" | "md";
  className?: string;
}

export function StatusBadge({ status, label, size = "sm", className }: StatusBadgeProps) {
  const meta = STATUS_META[status];
  const Icon = meta.Icon;
  return (
    <Badge
      tone={meta.tone}
      size={size}
      className={className}
      icon={<Icon size={11} />}
      title={label ?? meta.label}
    >
      {label ?? meta.label}
    </Badge>
  );
}
