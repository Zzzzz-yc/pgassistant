/**
 * Checkbox（需求文档 §9.4「基础」，禁止原生 checkbox 裸奔 → Radix Checkbox 封装）。
 *
 * 五态：
 * - hover：边框变 primary
 * - focus-visible：2px focus 环
 * - active：底色 `--primary-soft`
 * - disabled：`disabled` + opacity .45
 * - loading：勾选占位换成 Spinner（提交中的乐观勾选），**同时真的禁用**（`disabled` +
 *   `aria-disabled="true"` + `aria-busy="true"`），避免在提交中再点一次造成双写
 */
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check, Minus } from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export interface CheckboxProps {
  /** 标签（可点，与 checkbox 同一个 label 元素）。 */
  label?: ReactNode;
  checked: boolean | "indeterminate";
  onCheckedChange: (checked: boolean | "indeterminate") => void;
  disabled?: boolean;
  loading?: boolean;
  /** 不传时自动生成，用于把 label 绑到 checkbox 上。 */
  id?: string;
  className?: string;
  ariaLabel?: string;
}

export function Checkbox({
  label,
  checked,
  onCheckedChange,
  disabled = false,
  loading = false,
  id,
  className,
  ariaLabel,
}: CheckboxProps) {
  const autoId = useId();
  const boxId = id ?? autoId;
  const isDisabled = disabled || loading;

  return (
    <label className={cn("ui-checkbox", className)} htmlFor={boxId}>
      <CheckboxPrimitive.Root
        id={boxId}
        className="ui-checkbox__box"
        checked={checked}
        disabled={isDisabled}
        onCheckedChange={onCheckedChange}
        aria-label={label ? undefined : ariaLabel}
        aria-disabled={isDisabled || undefined}
        aria-busy={loading || undefined}
      >
        {loading ? null : (
          <CheckboxPrimitive.Indicator>
            {checked === "indeterminate" ? (
              <Minus size={12} aria-hidden="true" />
            ) : (
              <Check size={12} aria-hidden="true" />
            )}
          </CheckboxPrimitive.Indicator>
        )}
      </CheckboxPrimitive.Root>
      {loading ? <Spinner size="sm" label="" /> : null}
      {label ? <span>{label}</span> : null}
    </label>
  );
}
