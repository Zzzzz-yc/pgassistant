/**
 * TypeIcon（业务组件，需求文档 §9.4 资料类型）。
 *
 * 五态：hover / focus-visible / active / disabled / loading **全部不适用**——纯图形标记，
 * 不可交互。需要"可点的类型筛选"请用 `Tag` 或 `Combobox`。
 *
 * 口径（DECISIONS D19）：8 值映射表只决定**图标与文案**，不得当筛选器的选项来源。
 */
import {
  Book,
  BookOpen,
  Compass,
  FileText,
  GraduationCap,
  ListChecks,
  PencilRuler,
  Video,
  type LucideIcon,
} from "lucide-react";

import type { ResourceType } from "@/types/api";

interface TypeMeta {
  Icon: LucideIcon;
  label: string;
}

const TYPE_META: Record<ResourceType, TypeMeta> = {
  article: { Icon: FileText, label: "文章" },
  docs: { Icon: BookOpen, label: "文档" },
  exercise: { Icon: PencilRuler, label: "练习" },
  overview: { Icon: Compass, label: "导览" },
  summary: { Icon: ListChecks, label: "总结" },
  video: { Icon: Video, label: "视频" },
  course: { Icon: GraduationCap, label: "课程" },
  book: { Icon: Book, label: "书籍" },
};

/** 类型 → 中文名（8 值齐全；选项来源仍以接口返回为准）。 */
export function typeLabel(type: ResourceType | null | undefined): string {
  return type ? TYPE_META[type].label : "未知类型";
}

export interface TypeIconProps {
  type: ResourceType | null | undefined;
  /** 尺寸（px），默认 14。 */
  size?: number;
  /** 显示文字标签（默认只显示图标，读屏仍有 `aria-label`）。 */
  showLabel?: boolean;
  className?: string;
}

export function TypeIcon({ type, size = 14, showLabel = false, className }: TypeIconProps) {
  const meta = type ? TYPE_META[type] : null;
  const Icon = meta?.Icon ?? FileText;
  const label = meta?.label ?? "未知类型";

  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-1)",
        color: "var(--text-muted)",
        fontSize: 12,
      }}
      title={label}
    >
      <Icon size={size} aria-hidden="true" />
      {showLabel ? <span>{label}</span> : <span className="visually-hidden">{label}</span>}
    </span>
  );
}
