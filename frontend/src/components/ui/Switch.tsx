/**
 * Switch（需求文档 §9.4「基础」，禁止原生 checkbox 冒充开关 → Radix Switch）。
 *
 * 五态：
 * - hover：边框变 primary
 * - focus-visible：2px focus 环
 * - active：轨道底色加深
 * - disabled：`disabled` + opacity .45
 * - loading：**不适用**（开关是即时生效的本地状态；需要"提交中"时由父级用
 *   `disabled` + 旁边一个 Spinner 表达，见 SettingRow 用法）。理由写在这里而不是默默缺。
 */
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { useId, type ReactNode } from "react";

import { cn } from "./cn";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label?: ReactNode;
  /** 标签下的说明。 */
  hint?: ReactNode;
  disabled?: boolean;
  id?: string;
  className?: string;
  ariaLabel?: string;
}

export function Switch({
  checked,
  onCheckedChange,
  label,
  hint,
  disabled = false,
  id,
  className,
  ariaLabel,
}: SwitchProps) {
  const autoId = useId();
  const switchId = id ?? autoId;

  const control = (
    <SwitchPrimitive.Root
      id={switchId}
      className="ui-switch"
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      aria-label={label ? undefined : ariaLabel}
    >
      <SwitchPrimitive.Thumb className="ui-switch__thumb" />
    </SwitchPrimitive.Root>
  );

  if (!label && !hint) return <span className={className}>{control}</span>;

  return (
    <div className={cn("ui-field", className)} style={{ flexDirection: "row", alignItems: "center", gap: "var(--space-3)" }}>
      {control}
      <label htmlFor={switchId} style={{ cursor: disabled ? "not-allowed" : "pointer" }}>
        <span className="ui-field__label">{label}</span>
        {hint ? <span className="ui-field__hint" style={{ display: "block" }}>{hint}</span> : null}
      </label>
    </div>
  );
}
