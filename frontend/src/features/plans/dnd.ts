/**
 * 原生拖拽的小工具（计划模块 M3，§8.3 拖拽排序）。
 *
 * 为什么不用第三方 DnD 库：§3.1 的技术栈没有列它，RULES §7 也不允许临时加依赖。
 * 计划页的拖拽只有两种：阶段重排、条目在本阶段内重排 / 跨阶段移动。
 * 用 HTML5 `draggable` + `dataTransfer` 足够，而且**手柄才是 drag source**，
 * 行本身不用 `draggable`，避免误拖整行导致点击失效。
 *
 * `order_index` 的算法与后端一致（MODULES §2.3 第 4 条）：取相邻两条的中值；
 * 取不到中值（相邻差值 ≤1）时返回 `null`，交给页面用「整段重排」兜底。
 */
import type { DragEvent } from "react";

export const DRAG_MIME = "application/x-pgassistant-plan-node";

export type DragKind = "stage" | "item";

export interface DragPayload {
  kind: DragKind;
  /** 阶段 id 或条目 id。 */
  id: number;
  /** 条目专用：出发阶段的 id（跨阶段时用来判断"换阶段"而不是"换位置"）。 */
  fromStageId?: number;
}

/** 把 payload 写进 `dataTransfer`；同时声明这是 move，光标会变成"搬运"样式。 */
export function writeDragPayload(event: DragEvent, payload: DragPayload): void {
  event.dataTransfer.effectAllowed = "move";
  // 某些浏览器（Firefox）要求至少写一个 `text/plain` 才允许拖动
  event.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
  event.dataTransfer.setData("text/plain", String(payload.id));
}

/** 读 payload；不是本模块拖的东西（例如从浏览器拖进来的链接）返回 `null`。 */
export function readDragPayload(event: DragEvent): DragPayload | null {
  const raw = event.dataTransfer.getData(DRAG_MIME);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<DragPayload>;
    if ((parsed.kind !== "stage" && parsed.kind !== "item") || typeof parsed.id !== "number") {
      return null;
    }
    return {
      kind: parsed.kind,
      id: parsed.id,
      fromStageId: typeof parsed.fromStageId === "number" ? parsed.fromStageId : undefined,
    };
  } catch {
    return null;
  }
}

/** 拖到目标上方还是下方：鼠标在目标行上半 → before，下半 → after。 */
export function dropEdge(event: DragEvent<HTMLElement>): "before" | "after" {
  const rect = event.currentTarget.getBoundingClientRect();
  return event.clientY < rect.top + rect.height / 2 ? "before" : "after";
}

/**
 * 相邻两点之间取中值（后端同样算法）。
 * 返回 `null` 表示"缝隙用完了"，调用方应改走整段重排（1000 步长）。
 */
export function middleOrderIndex(
  before: number | undefined,
  after: number | undefined,
): number | null {
  if (before !== undefined && after !== undefined) {
    const middle = Math.floor((before + after) / 2);
    return middle > before && middle < after ? middle : null;
  }
  if (before !== undefined) return before + 1000;
  if (after !== undefined) return Math.max(0, Math.floor(after / 2));
  return 1000;
}

/**
 * 整段重排：把当前顺序按 1000 步长重新编号，返回 id → order_index。
 * 只在 `middleOrderIndex` 返回 `null` 时使用（连续插在同一缝隙几十次后才会发生）。
 */
export function renumber<T extends { id: number }>(rows: readonly T[]): Map<number, number> {
  return new Map(rows.map((row, index) => [row.id, (index + 1) * 1000]));
}

/** 键盘兜底（§9.7 所有交互可 Tab 到达）：手柄上按 ↑/↓ 与拖拽等价。 */
export function orderDirection(key: string): -1 | 0 | 1 {
  if (key === "ArrowUp") return -1;
  if (key === "ArrowDown") return 1;
  return 0;
}
