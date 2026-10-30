/**
 * ResourceCard（业务组件，需求文档 §9.4：封面或首字块 + 标题两行截断 + 摘要两行 +
 * 标签 + 时长 + 难度徽章；hover 浮现「加入计划」快捷按钮）。
 *
 * 边界：**纯展示**。数据全部由 props 传入；不发请求、不读路由、不读全局状态。
 * 需要跳转详情页时：
 * - 只传 `href`：渲染普通 `<a>`（整页跳转）；
 * - 传 `href` + `onOpen`：`preventDefault` 后交给调用方（资料页用 `navigate()` 做 SPA 跳转）。
 *
 * 五态：
 * - hover：卡片上浮 2px + 边框向 primary（`Card` 的 hoverable）+ 「加入计划」浮现
 * - focus-visible：标题链接/按钮各自的 2px 环；`focus-within` 同样让快捷按钮浮现（键盘可达）
 * - active：卡片回位 + `--primary-soft` 底
 * - disabled：**不适用**（卡片不会被禁用；不可用的动作由按钮自身禁用）
 * - loading：「加入计划」正在提交时给 `addToPlanLoading`，按钮转 Spinner；封面图加载中
 *   先铺 `--primary-soft` 底色，失败退回首字块
 */
import { Plus } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, Tag, cn } from "@/components/ui";
import { type Level, type ResourceSource, type ResourceType } from "@/types/api";

import { DurationLabel } from "./DurationLabel";
import { TypeIcon } from "./TypeIcon";
import "./business.css";

export interface ResourceCardProps {
  title: string;
  summary?: string;
  /** 详情页地址（slug 含 `/`，由调用方拼好，组件不猜前缀）。 */
  href?: string;
  /** 点击标题时的回调（给了就接管跳转）。 */
  onOpen?: () => void;
  /** 已解析好的封面 URL；为空或加载失败时用首字块。 */
  coverUrl?: string | null;
  tags?: readonly string[];
  durationMin?: number | null;
  level?: Level | null;
  type?: ResourceType | null;
  source?: ResourceSource | null;
  /** 传了才渲染「加入计划」按钮。 */
  onAddToPlan?: () => void;
  addToPlanLoading?: boolean;
  addToPlanLabel?: string;
  /** 最多显示几个标签，其余收成 `+N`。 */
  maxTags?: number;
  /** 画廊专用：把 hover 态固定下来（生产不要传）。 */
  forceHover?: boolean;
  className?: string;
}

const LEVEL_LABEL: Record<Level, string> = {
  beginner: "入门",
  intermediate: "进阶",
  advanced: "高级",
};

function initialOf(title: string): string {
  const trimmed = title.trim();
  return trimmed ? (Array.from(trimmed)[0] ?? "·") : "·";
}

export function ResourceCard({
  title,
  summary,
  href,
  onOpen,
  coverUrl,
  tags = [],
  durationMin,
  level,
  type,
  source,
  onAddToPlan,
  addToPlanLoading = false,
  addToPlanLabel = "加入计划",
  maxTags = 3,
  forceHover = false,
  className,
}: ResourceCardProps) {
  const [coverBroken, setCoverBroken] = useState(false);
  const showCover = Boolean(coverUrl) && !coverBroken;
  const visibleTags = tags.slice(0, Math.max(0, maxTags));
  const hiddenTagCount = tags.length - visibleTags.length;

  const titleNode = href ? (
    <a
      className="rc__title-link"
      href={href}
      onClick={(event) => {
        if (!onOpen) return;
        event.preventDefault();
        onOpen();
      }}
    >
      {title}
    </a>
  ) : onOpen ? (
    <a
      className="rc__title-link"
      href="#"
      onClick={(event) => {
        event.preventDefault();
        onOpen();
      }}
    >
      {title}
    </a>
  ) : (
    title
  );

  return (
    <Card
      padded={false}
      hoverable
      className={cn("rc", forceHover && "rc--force-hover", className)}
    >
      <div className="rc__cover">
        {showCover ? (
          <img
            className="rc__cover-img"
            src={coverUrl ?? undefined}
            alt=""
            loading="lazy"
            onError={() => setCoverBroken(true)}
          />
        ) : (
          <span className="rc__initial" aria-hidden="true">
            {initialOf(title)}
          </span>
        )}

        {source === "private" ? (
          <span className="rc__source">
            <Badge tone="accent">私有</Badge>
          </span>
        ) : null}

        {onAddToPlan ? (
          <span className="rc__add">
            <Button
              size="sm"
              variant="secondary"
              loading={addToPlanLoading}
              iconLeft={<Plus size={13} />}
              onClick={onAddToPlan}
            >
              {addToPlanLabel}
            </Button>
          </span>
        ) : null}
      </div>

      <div className="rc__body">
        <h3 className="rc__title">{titleNode}</h3>
        {summary ? <p className="rc__summary">{summary}</p> : null}

        {visibleTags.length > 0 ? (
          <ul className="rc__tags">
            {visibleTags.map((tag) => (
              <li key={tag}>
                <Tag>{tag}</Tag>
              </li>
            ))}
            {hiddenTagCount > 0 ? (
              <li>
                <Tag>+{hiddenTagCount}</Tag>
              </li>
            ) : null}
          </ul>
        ) : null}

        <div className="rc__meta">
          <TypeIcon type={type} />
          <DurationLabel minutes={durationMin} />
          <span className="rc__meta-end">
            {level ? <Badge tone="accent" title={`难度：${LEVEL_LABEL[level]}`}>{LEVEL_LABEL[level]}</Badge> : null}
          </span>
        </div>
      </div>
    </Card>
  );
}
