/**
 * 组件库出口（Owner：组件库 M2，docs/contracts/MODULES.md §1.2）。
 *
 * 约定：
 * - `components/ui/**` 只有组件库能改；页面模块只**组合**、传 props，不改组件内部。
 * - 组件只接受 props：不发请求、不读路由、不读写 localStorage、不 import `lib/api/*`。
 *   唯一例外是 toast 的**展示**组件——它也只吃 props，队列在 `lib/query.ts`。
 * - 样式全在 `./ui.css`，只使用 `src/styles/tokens.css` 的变量，本目录零色值字面量。
 *
 * 五态口径（每个组件文件顶部都写了适用/不适用的理由）：
 * hover / focus-visible / active / disabled / loading。
 */
import "./ui.css";

export { cn } from "./cn";
export type { FieldSize } from "./field";
export { restoreFocus, useReturnFocus } from "./focus";
export type { ReturnFocusHandlers } from "./focus";

export { Accordion } from "./Accordion";
export type { AccordionItem, AccordionProps } from "./Accordion";

export { Avatar } from "./Avatar";
export type { AvatarProps, AvatarSize } from "./Avatar";

export { Badge } from "./Badge";
export type { BadgeProps, BadgeSize, BadgeTone } from "./Badge";

export { Button } from "./Button";
export type { ButtonProps, ButtonSize, ButtonVariant } from "./Button";

export { Card } from "./Card";
export type { CardProps } from "./Card";

export { Checkbox } from "./Checkbox";
export type { CheckboxProps } from "./Checkbox";

export { Combobox } from "./Combobox";
export type { ComboboxOption, ComboboxProps } from "./Combobox";

export { Dialog } from "./Dialog";
export type { DialogProps } from "./Dialog";

export { DropdownMenu } from "./DropdownMenu";
export type { DropdownMenuProps, MenuEntry } from "./DropdownMenu";

export { IconButton } from "./IconButton";
export type { IconButtonProps } from "./IconButton";

export { Input } from "./Input";
export type { InputProps } from "./Input";

export { Pagination } from "./Pagination";
export type { PaginationProps } from "./Pagination";

export { Panel } from "./Panel";
export type { PanelProps } from "./Panel";

export { Popover } from "./Popover";
export type { PopoverProps } from "./Popover";

export { ProgressBar } from "./ProgressBar";
export type { ProgressBarProps, ProgressTone } from "./ProgressBar";

export { ProgressRing } from "./ProgressRing";
export type { ProgressRingProps, RingSize } from "./ProgressRing";

export { Radio, RadioGroup } from "./Radio";
export type { RadioGroupProps, RadioOption, RadioProps } from "./Radio";

export { Select } from "./Select";
export type { SelectOption, SelectProps } from "./Select";

export { Separator } from "./Separator";
export type { SeparatorProps } from "./Separator";

export { Sheet } from "./Sheet";
export type { SheetProps, SheetSide } from "./Sheet";

export { Skeleton } from "./Skeleton";
export type { SkeletonProps, SkeletonShape } from "./Skeleton";

export { Slider } from "./Slider";
export type { SliderProps } from "./Slider";

export { Spinner } from "./Spinner";
export type { SpinnerProps, SpinnerSize } from "./Spinner";

export { Switch } from "./Switch";
export type { SwitchProps } from "./Switch";

export { Tabs } from "./Tabs";
export type { TabItem, TabsProps } from "./Tabs";

export { Tag } from "./Tag";
export type { TagProps } from "./Tag";

export { Textarea } from "./Textarea";
export type { TextareaProps } from "./Textarea";

export { Toast, ToastStack } from "./Toast";
export type { ToastKind, ToastProps } from "./Toast";

export { Tooltip, TooltipProvider } from "./Tooltip";
export type { TooltipProps } from "./Tooltip";
