/**
 * Pagination（需求文档 §9.4「基础」：页码 + 省略号 + 上下页）。
 *
 * 五态：
 * - hover：底色 `--bg` + 边框
 * - focus-visible：2px focus 环
 * - active：`--primary-soft` 底
 * - disabled/loading：上一页/下一页在边界或加载中禁用（opacity .4 + `cursor: not-allowed`）
 * 注：页码按钮本身没有 loading（翻页中由父级给 `loading`，禁用整组避免连点）。
 *
 * 无障碍：`<nav aria-label>` 包一层；当前页 `aria-current="page"`；省略号是文本，不可点。
 */
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "./cn";
import { IconButton } from "./IconButton";

export interface PaginationProps {
  /** 1 基页码。 */
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
  /** 当前页两侧各显示几页，默认 1。 */
  siblingCount?: number;
  /** 翻页请求进行中：整组禁用，避免连点。 */
  loading?: boolean;
  labels?: { previous?: string; next?: string; navigation?: string };
  className?: string;
}

type PageSlot = number | "ellipsis-start" | "ellipsis-end";

/** 生成页码槽位：总是保留首页/尾页与当前页左右的 sibling。 */
function buildSlots(page: number, totalPages: number, siblingCount: number): PageSlot[] {
  const totalNumbers = siblingCount * 2 + 5; // 首 + 尾 + 当前 + 两侧 + 两个省略号
  if (totalPages <= totalNumbers) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const left = Math.max(page - siblingCount, 1);
  const right = Math.min(page + siblingCount, totalPages);
  const showStartEllipsis = left > 2;
  const showEndEllipsis = right < totalPages - 1;

  const slots: PageSlot[] = [1];
  if (showStartEllipsis) slots.push("ellipsis-start");

  const from = showStartEllipsis ? left : 2;
  const to = showEndEllipsis ? right : totalPages - 1;
  for (let value = from; value <= to; value += 1) slots.push(value);

  if (showEndEllipsis) slots.push("ellipsis-end");
  slots.push(totalPages);
  return slots;
}

export function Pagination({
  page,
  totalPages,
  onChange,
  siblingCount = 1,
  loading = false,
  labels,
  className,
}: PaginationProps) {
  const safeTotal = Math.max(1, totalPages);
  const safePage = Math.min(Math.max(1, page), safeTotal);
  const slots = buildSlots(safePage, safeTotal, Math.max(0, siblingCount));

  return (
    <nav className={cn("ui-pagination", className)} aria-label={labels?.navigation ?? "分页"}>
      <IconButton
        label={labels?.previous ?? "上一页"}
        icon={<ChevronLeft size={16} />}
        size="md"
        variant="ghost"
        disabled={safePage <= 1 || loading}
        onClick={() => onChange(safePage - 1)}
      />

      {slots.map((slot) => {
        if (typeof slot !== "number") {
          return (
            <span key={slot} className="ui-page ui-page--ellipsis" aria-hidden="true">
              …
            </span>
          );
        }
        const current = slot === safePage;
        return (
          <button
            key={slot}
            type="button"
            className="ui-page"
            aria-current={current ? "page" : undefined}
            aria-label={current ? `第 ${slot} 页（当前页）` : `第 ${slot} 页`}
            disabled={loading}
            onClick={() => {
              if (!current) onChange(slot);
            }}
          >
            {slot}
          </button>
        );
      })}

      <IconButton
        label={labels?.next ?? "下一页"}
        icon={<ChevronRight size={16} />}
        size="md"
        variant="ghost"
        disabled={safePage >= safeTotal || loading}
        onClick={() => onChange(safePage + 1)}
      />
    </nav>
  );
}
