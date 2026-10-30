/**
 * 运行开关与「能力探测」（需求文档 §20.5 第 7 条 / §20.6 第 1 行）。
 *
 * 设置页 UI 归打磨模块（M5），本模块**只读取、只监听**：
 *
 * | 契约 | 值 |
 * |---|---|
 * | localStorage 键 | `runner_enabled`（`"true"` / `"false"`，缺省 = 开启，§20.5-7“默认开启”） |
 * | 同标签页通知 | `window.dispatchEvent(new Event("pgassistant:runner-settings"))` |
 * | 跨标签页通知 | 浏览器原生 `storage` 事件（M5 无需额外做） |
 *
 * 关闭后所有运行入口（RunButton、抽屉）**消失**，不是禁用；页面其它功能不受影响。
 */
import { useEffect, useState } from "react";

/** localStorage 键：总开关。 */
export const RUNNER_ENABLED_KEY = "runner_enabled";

/** 同标签页内的设置变更事件名（M5 写完 localStorage 后 dispatch 一次即可）。 */
export const RUNNER_SETTINGS_EVENT = "pgassistant:runner-settings";

/** localStorage 键：首次运行说明卡「不再提示」。 */
export const RUNNER_INTRO_ACK_KEY = "pgassistant.runner.intro_ack";

function readFlag(key: string, fallback: boolean): boolean {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    return raw !== "false" && raw !== "0";
  } catch {
    // 隐私模式 / 存储被禁：按 fallback 处理，绝不因为读不到就抛
    return fallback;
  }
}

/** 当前是否开启站内运行（默认开启）。 */
export function isRunnerEnabled(): boolean {
  return readFlag(RUNNER_ENABLED_KEY, true);
}

/** 首次说明卡是否已被「不再提示」。 */
export function isIntroAcknowledged(): boolean {
  return readFlag(RUNNER_INTRO_ACK_KEY, false);
}

export function acknowledgeIntro(): void {
  try {
    window.localStorage.setItem(RUNNER_INTRO_ACK_KEY, "true");
  } catch {
    // 存不下就下次再弹，不影响运行
  }
}

/**
 * 浏览器是否具备运行条件（§20.6「浏览器不支持 WASM / Worker → 隐藏运行按钮」）。
 * 判断放在每次调用时做，方便测试里打桩。
 */
export function isRunnerSupported(): boolean {
  return (
    typeof Worker !== "undefined" &&
    typeof WebAssembly === "object" &&
    WebAssembly !== null &&
    typeof WebAssembly.instantiate === "function"
  );
}

/** 供组件使用的开关 + 能力快照；设置变更会触发重渲染。 */
export function useRunnerEnabled(): { enabled: boolean; supported: boolean } {
  const [enabled, setEnabled] = useState(isRunnerEnabled);
  const [supported] = useState(isRunnerSupported);

  useEffect(() => {
    const sync = () => setEnabled(isRunnerEnabled());
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === RUNNER_ENABLED_KEY) sync();
    };
    window.addEventListener(RUNNER_SETTINGS_EVENT, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(RUNNER_SETTINGS_EVENT, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return { enabled, supported };
}
