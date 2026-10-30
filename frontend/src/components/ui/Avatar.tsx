/**
 * Avatar（需求文档 §9.4「基础」）。
 *
 * 五态：hover / focus-visible / active / disabled **不适用**——头像是静态图形，
 * 不带动作；loading 用 `loading` 渲染成圆形 Skeleton（图片正在取）。
 * 无障碍：有 `src` 时 `alt` 参与读屏；退化到首字块时读屏靠 `name` 的 `aria-label`。
 */
import * as AvatarPrimitive from "@radix-ui/react-avatar";

import { cn } from "./cn";
import { Skeleton } from "./Skeleton";

export type AvatarSize = "sm" | "md" | "lg";

export interface AvatarProps {
  /** 图片地址（后端静态资源或外链）。 */
  src?: string | null;
  /** 用户名：既做 `alt`，也做首字块的来源。 */
  name: string;
  size?: AvatarSize;
  loading?: boolean;
  className?: string;
}

function initials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  // 中文名取最后一字（"张三" → "三"），拉丁名取首字母（"Ada Lovelace" → "A"）
  const first = Array.from(trimmed)[0] ?? "?";
  return /[\u4e00-\u9fa5]/.test(trimmed) ? first : first.toUpperCase();
}

export function Avatar({ src, name, size = "md", loading = false, className }: AvatarProps) {
  const classes = cn("ui-avatar", size !== "md" && `ui-avatar--${size}`, className);

  if (loading) {
    return (
      <span className={classes} aria-hidden="true">
        <Skeleton shape="circle" width="100%" height="100%" />
      </span>
    );
  }

  return (
    <AvatarPrimitive.Root className={classes} aria-label={name}>
      {src ? (
        <AvatarPrimitive.Image className="ui-avatar__image" src={src} alt={name} />
      ) : null}
      <AvatarPrimitive.Fallback>{initials(name)}</AvatarPrimitive.Fallback>
    </AvatarPrimitive.Root>
  );
}
