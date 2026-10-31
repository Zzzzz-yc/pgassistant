/**
 * 助手会话的**模块级消息存储**（Owner：助手模块 M4）。
 *
 * 为什么是模块级 store，而不是 `sessionStorage`：
 * 1. 需求只要求「本次会话内不丢」——切路由 / 来回跳页面还在，**关闭标签页或刷新即清空**
 *    （「刷新后恢复历史」是后续独立需求，本轮不做）；
 * 2. 消息里可能有计划草稿、检索结果，写进浏览器存储等于把业务数据持久化到磁盘，
 *    还多了配额、反序列化脏数据与令牌误入的风险（§13.7）。模块级内存天然没有这些问题；
 * 3. SPA 里模块只求值一次，路由卸载 `pages/assistant` 不会丢掉这个模块的状态。
 *
 * 容量上限（需求 C1）：最多保留最近 `MAX_MESSAGES` 条，序列化后总量 ≤ `MAX_BYTES`，
 * 超限**从最旧丢弃**（并且尽量不留下"助手消息开头"的孤儿）。
 *
 * 流式状态也放在这里：切页面时 hook 的清理函数会把在飞的消息标成 `aborted` 并落库到
 * store，回来时不会留下永远不会结束的「正在输入」气泡。
 */
import type { AssistantMessage } from "./types";

/** 最多保留多少条消息。 */
export const MAX_MESSAGES = 50;
/** 消息序列化后的总量上限（1 MB）。 */
export const MAX_BYTES = 1_000_000;

export interface ThreadState {
  messages: AssistantMessage[];
  /** 是否有流在飞（页面据此显示「中止」而不是「发送」）。 */
  streaming: boolean;
}

type Listener = () => void;

let state: ThreadState = { messages: [], streaming: false };
let seq = 0;
const listeners = new Set<Listener>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

/** `useSyncExternalStore` 的订阅入口。 */
export function subscribeThread(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** `useSyncExternalStore` 的快照：只在状态变化时换新对象，保证引用稳定。 */
export function getThreadSnapshot(): ThreadState {
  return state;
}

/** 队列内唯一 id（跨路由卸载后也不重复，避免 React key 冲突与保存去重误判）。 */
export function nextMessageId(role: "u" | "a"): string {
  seq += 1;
  return `${role}-${seq}`;
}

const encoder = new TextEncoder();

function byteSize(messages: readonly AssistantMessage[]): number {
  let total = 0;
  for (const message of messages) {
    try {
      total += encoder.encode(JSON.stringify(message)).length;
    } catch {
      // 循环引用等异常：按一个粗估上限记账，保证裁剪逻辑仍然推进
      total += MAX_BYTES;
    }
  }
  return total;
}

/**
 * 裁剪：条数 ≤ `maxCount` 且序列化总量 ≤ `maxBytes`，**从最旧丢起**。
 *
 * 丢掉最早一条之后如果剩下的第一条是助手消息，再顺手丢掉它——否则线程会以一句
 * 没有问题的回答开头。纯函数，便于单测；不改入参。
 */
export function trimMessages(
  messages: readonly AssistantMessage[],
  maxCount: number = MAX_MESSAGES,
  maxBytes: number = MAX_BYTES,
): AssistantMessage[] {
  let list = messages.slice();
  let bytes = byteSize(list);

  while (list.length > 1 && (list.length > maxCount || bytes > maxBytes)) {
    const [dropped, ...rest] = list;
    bytes -= byteSize([dropped]);
    list = rest;
  }
  // 只剩 1 条时即使超限也保留：一条消息不至于把内存撑爆
  if (list.length > 1 && list[0].role === "assistant") {
    list = list.slice(1);
  }
  return list;
}

/** 追加 / 替换消息（自动裁剪并通知订阅者）。 */
export function setThreadMessages(
  updater: (messages: AssistantMessage[]) => AssistantMessage[],
): void {
  const next = trimMessages(updater(state.messages));
  state = { ...state, messages: next };
  emit();
}

/** 按 id 改一条消息；不存在的 id 视为无操作。 */
export function patchThreadMessage(
  id: string,
  update: (message: AssistantMessage) => AssistantMessage,
): void {
  let changed = false;
  const next = state.messages.map((message) => {
    if (message.id !== id) return message;
    changed = true;
    return update(message);
  });
  if (!changed) return;
  state = { ...state, messages: next };
  emit();
}

export function setThreadStreaming(streaming: boolean): void {
  if (state.streaming === streaming) return;
  state = { ...state, streaming };
  emit();
}

/** 清空（切账号/测试用；页面不调用它——刷新与关标签页天然清空）。 */
export function resetThread(): void {
  state = { messages: [], streaming: false };
  seq = 0;
  emit();
}
