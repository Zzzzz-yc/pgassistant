/**
 * 数据层与共享 hooks（docs/contracts/MODULES.md §3.3）。
 *
 * 这里只放**跨模块**的：QueryClient、health / taxonomy 两个只读查询、
 * 令牌读取、防抖、快捷键、Toast。业务域 hooks 在各自的 `features/<域>/hooks.ts`。
 */
import {
  QueryClient,
  useQuery,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { getHealth } from "@/lib/api/system";
import { appToken, request } from "@/lib/http";
import type { HealthResponse, TaxonomyResponse } from "@/types/api";

/** 全局 QueryClient：失败重试 1 次、30s 内视为新鲜（§3.3 约定）。 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

/* --------------------------------------------------------------- 只读查询 */

/** 存活探针。免令牌，因此后端没起来时只会是网络错误。 */
export function useHealth(): UseQueryResult<HealthResponse> {
  return useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });
}

/**
 * 分类树。`getTaxonomy()` 的文件归属是资料页（M2，`lib/api/resources.ts`），
 * M0 不占用那个文件，所以这里直接用传输层取；M2 落地后可改为复用其函数，
 * hook 的 key 与返回类型不变。
 */
export function useTaxonomy(): UseQueryResult<TaxonomyResponse> {
  return useQuery({
    queryKey: ["taxonomy"],
    queryFn: () => request<TaxonomyResponse>("GET", "/api/taxonomy"),
  });
}

/* ------------------------------------------------------------------ 令牌 */

/**
 * 读页面里的 `X-App-Token`（不走网络、不进任何存储）。
 * 令牌随服务重启失效；失效时 `http.ts` 会派发 `app:reload-required`，UI 引导刷新。
 */
export function useAppToken(): string {
  const [token, setToken] = useState<string>(() => appToken());
  useEffect(() => {
    // meta 只可能在页面重新加载后变化，这里只在挂载时校准一次
    setToken((current) => current || appToken());
  }, []);
  return token;
}

/* ------------------------------------------------------------------ 防抖 */

/** 输入防抖：搜索框、筛选器。默认 250ms。 */
export function useDebouncedValue<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), Math.max(0, delayMs));
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/* -------------------------------------------------------------- 快捷键 */

export interface HotkeyBinding {
  /** 形如 `"mod+k"`、`"escape"`、`"shift+/"`；`mod` = macOS ⌘ / 其它 Ctrl。 */
  combo: string;
  handler: (event: KeyboardEvent) => void;
  /** 在输入框里也生效（默认 false，避免抢走打字）。 */
  allowInInput?: boolean;
}

const MODIFIER_ALIASES: Record<string, "ctrl" | "meta" | "alt" | "shift"> = {
  mod: "ctrl",
  ctrl: "ctrl",
  control: "ctrl",
  cmd: "meta",
  command: "meta",
  meta: "meta",
  alt: "alt",
  option: "alt",
  shift: "shift",
};

interface ParsedCombo {
  key: string;
  ctrl: boolean;
  meta: boolean;
  alt: boolean;
  shift: boolean;
  mod: boolean;
}

function parseCombo(combo: string): ParsedCombo {
  const parsed: ParsedCombo = {
    key: "",
    ctrl: false,
    meta: false,
    alt: false,
    shift: false,
    mod: false,
  };
  for (const raw of combo.toLowerCase().split("+")) {
    const part = raw.trim();
    if (!part) continue;
    if (part === "mod") parsed.mod = true;
    const modifier = MODIFIER_ALIASES[part];
    if (modifier) {
      parsed[modifier] = true;
    } else {
      parsed.key = part;
    }
  }
  return parsed;
}

const IS_MAC = typeof navigator !== "undefined" && /mac|iphone|ipad/i.test(navigator.platform);

function matches(event: KeyboardEvent, combo: ParsedCombo): boolean {
  const key = event.key.toLowerCase();
  if (combo.key && key !== combo.key) return false;
  const wantCtrl = combo.ctrl || (combo.mod && !IS_MAC);
  const wantMeta = combo.meta || (combo.mod && IS_MAC);
  if (event.ctrlKey !== wantCtrl) return false;
  if (event.metaKey !== wantMeta) return false;
  if (event.altKey !== combo.alt) return false;
  if (event.shiftKey !== combo.shift) return false;
  return true;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
}

/**
 * 注册快捷键。绑定数组可以是每次渲染新建的字面量：内部用 ref 取最新值，
 * 监听器只挂一次（避免反复解绑）。
 */
export function useHotkeys(bindings: HotkeyBinding[]): void {
  const latest = useRef(bindings);
  latest.current = bindings;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      for (const binding of latest.current) {
        if (!binding.combo) continue;
        if (!matches(event, parseCombo(binding.combo))) continue;
        if (!binding.allowInInput && isEditableTarget(event.target)) continue;
        event.preventDefault();
        binding.handler(event);
        return;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}

/* ------------------------------------------------------------------ Toast */

export type ToastKind = "info" | "success" | "warn" | "error";

export interface ToastInput {
  message: string;
  hint?: string;
  kind?: ToastKind;
  /** 自动消失时间，默认 4s；`0` 表示不自动消失。 */
  ttlMs?: number;
}

export interface ToastItem extends ToastInput {
  id: number;
  kind: ToastKind;
}

type ToastListener = (items: readonly ToastItem[]) => void;

const DEFAULT_TOAST_TTL = 4_000;
let toastSeq = 0;
let toastItems: readonly ToastItem[] = [];
const toastListeners = new Set<ToastListener>();

function emitToasts(): void {
  for (const listener of toastListeners) listener(toastItems);
}

/** ToastHost 用：订阅列表变化（`useSyncExternalStore` 的 subscribe）。 */
export function subscribeToasts(listener: ToastListener): () => void {
  toastListeners.add(listener);
  return () => {
    toastListeners.delete(listener);
  };
}

/** ToastHost 用：当前列表（`useSyncExternalStore` 的 getSnapshot，引用稳定）。 */
export function getToasts(): readonly ToastItem[] {
  return toastItems;
}

export function dismissToast(id: number): void {
  const next = toastItems.filter((item) => item.id !== id);
  if (next.length === toastItems.length) return;
  toastItems = next;
  emitToasts();
}

/**
 * 推一条 Toast。任何模块（含非组件代码）都可以用 `pushToast`；
 * 组件里优先用 `useToast()` 拿稳定引用。
 */
export function pushToast(input: ToastInput | string): number {
  const normalized: ToastInput = typeof input === "string" ? { message: input } : input;
  const id = ++toastSeq;
  const item: ToastItem = { id, kind: normalized.kind ?? "info", ...normalized };
  toastItems = [...toastItems, item];
  emitToasts();
  const ttl = normalized.ttlMs ?? DEFAULT_TOAST_TTL;
  if (ttl > 0) {
    window.setTimeout(() => dismissToast(id), ttl);
  }
  return id;
}

export interface ToastApi {
  push: (input: ToastInput | string) => number;
  dismiss: (id: number) => void;
}

/** 契约返回 `{push}`；额外给 `dismiss` 以便撤销/手动关闭。 */
export function useToast(): ToastApi {
  return useMemo<ToastApi>(
    () => ({ push: pushToast, dismiss: dismissToast }),
    [],
  );
}

/** 给非组件代码用的稳定引用（例如启动期错误提示）。 */
export const toast = { push: pushToast, dismiss: dismissToast } as const;
