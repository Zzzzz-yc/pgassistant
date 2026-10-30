/**
 * Combobox（需求文档 §9.4「基础」：可搜索下拉，禁止原生 select 裸奔）。
 *
 * 键盘契约：↑ / ↓ 移动高亮、Enter 选中、Esc 收起（只收下拉，不关外层弹层——
 * 这里 `stopPropagation`，避免和命令面板的 Esc 抢事件）、Tab 离开时收起。
 *
 * 五态：
 * - hover：选项底色 `--bg`、输入框边框向 primary 靠
 * - focus-visible：输入框 2px 环；高亮的选项有描边（roving 焦点用 `aria-activedescendant`，不真的移焦点）
 * - active：选项 `--primary-soft-hover`
 * - disabled：整组禁用 + opacity .45
 * - loading：输入框右侧 Spinner，`aria-busy`，且不展开列表
 *
 * 无障碍：输入框 `role="combobox"` + `aria-expanded` / `aria-controls` / `aria-autocomplete="list"`；
 * 列表 `role="listbox"`，选项 `role="option"`；无结果时给 `role="status"` 的提示行。
 */
import { ChevronDown, Search } from "lucide-react";
import { useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export interface ComboboxOption {
  value: string;
  label: string;
  /** 选项右侧的次要信息（如条目数）。 */
  description?: string;
  disabled?: boolean;
}

export interface ComboboxProps {
  options: readonly ComboboxOption[];
  /** 受控选中值（`undefined` = 未选）。 */
  value?: string;
  onChange: (value: string) => void;
  label?: ReactNode;
  hint?: ReactNode;
  placeholder?: string;
  /** 无匹配时的文案。 */
  emptyText?: string;
  disabled?: boolean;
  loading?: boolean;
  ariaLabel?: string;
  className?: string;
}

export function Combobox({
  options,
  value,
  onChange,
  label,
  hint,
  placeholder = "输入以搜索…",
  emptyText = "没有匹配项",
  disabled = false,
  loading = false,
  ariaLabel,
  className,
}: ComboboxProps) {
  const autoId = useId();
  const inputId = `${autoId}-input`;
  const listId = `${autoId}-list`;
  const hintId = `${autoId}-hint`;
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = useMemo(() => options.find((option) => option.value === value), [options, value]);
  const [query, setQuery] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  // 正在输入时显示输入内容，否则显示已选项的标签
  const text = query ?? selected?.label ?? "";

  const filtered = useMemo(() => {
    const needle = (query ?? "").trim().toLowerCase();
    if (!needle || needle === selected?.label.toLowerCase()) return options;
    return options.filter(
      (option) =>
        option.label.toLowerCase().includes(needle) || option.value.toLowerCase().includes(needle),
    );
  }, [options, query, selected]);

  const expanded = open && !disabled && !loading;

  function commit(option: ComboboxOption) {
    if (option.disabled) return;
    onChange(option.value);
    setQuery(null);
    setOpen(false);
    setActiveIndex(0);
  }

  function move(delta: number) {
    if (filtered.length === 0) return;
    const next = (activeIndex + delta + filtered.length) % filtered.length;
    setActiveIndex(next);
    const node = document.getElementById(`${listId}-option-${next}`);
    node?.scrollIntoView({ block: "nearest" });
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!expanded) setOpen(true);
      move(1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!expanded) setOpen(true);
      move(-1);
      return;
    }
    if (event.key === "Enter") {
      if (!expanded) return;
      event.preventDefault();
      const option = filtered[activeIndex];
      if (option) commit(option);
      return;
    }
    if (event.key === "Escape") {
      if (!expanded) return;
      event.preventDefault();
      event.stopPropagation(); // 只收下拉，不关外层弹层
      setOpen(false);
      setQuery(null);
      return;
    }
    if (event.key === "Tab") {
      setOpen(false);
      setQuery(null);
    }
  }

  return (
    <div className={cn("ui-field", className)}>
      {label ? (
        <label className="ui-field__label" htmlFor={inputId}>
          {label}
        </label>
      ) : null}

      <div className="ui-combobox">
        <div className="ui-input-wrap">
          <span className="ui-input__icon" aria-hidden="true">
            <Search size={15} />
          </span>
          <input
            ref={inputRef}
            id={inputId}
            role="combobox"
            aria-expanded={expanded}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={expanded ? `${listId}-option-${activeIndex}` : undefined}
            aria-label={label ? undefined : ariaLabel}
            aria-describedby={hint ? hintId : undefined}
            aria-busy={loading || undefined}
            className="ui-input ui-input--with-icon"
            placeholder={placeholder}
            disabled={disabled || loading}
            value={text}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
              setActiveIndex(0);
            }}
            onFocus={() => {
              if (!disabled && !loading) setOpen(true);
            }}
            onBlur={() => {
              // 让选项的 mousedown 先落地再收起
              window.setTimeout(() => {
                setOpen(false);
                setQuery(null);
              }, 120);
            }}
            onKeyDown={onKeyDown}
          />
          <span className="ui-input__trailing" aria-hidden="true">
            {loading ? <Spinner size="sm" /> : <ChevronDown size={15} />}
          </span>
        </div>

        {expanded ? (
          <ul className="ui-combobox__list" id={listId} role="listbox" aria-label={ariaLabel ?? "可选项"}>
            {filtered.length === 0 ? (
              <li className="ui-combobox__empty" role="status">
                {emptyText}
              </li>
            ) : (
              filtered.map((option, index) => (
                <li key={option.value}>
                  <button
                    type="button"
                    id={`${listId}-option-${index}`}
                    role="option"
                    aria-selected={option.value === value}
                    aria-disabled={option.disabled || undefined}
                    className="ui-combobox__option"
                    tabIndex={-1}
                    onMouseEnter={() => setActiveIndex(index)}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      commit(option);
                    }}
                  >
                    <span>{option.label}</span>
                    {option.description ? (
                      <span className="tnum" style={{ marginLeft: "auto", color: "var(--text-muted)" }}>
                        {option.description}
                      </span>
                    ) : null}
                  </button>
                </li>
              ))
            )}
          </ul>
        ) : null}
      </div>

      {hint ? (
        <div className="ui-field__hint" id={hintId}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}
