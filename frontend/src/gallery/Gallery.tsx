/**
 * 组件画廊（dev only，`/gallery.html`；不进 `npm run build` 产物）。
 *
 * 目的（需求文档 §9.4「每个都必须有完整五态」+ §9.2「素，但组件不能简陋」）：
 * 把 `components/ui/**` 与 `components/business/**` 一次铺开，**每个交互组件都摆出
 * hover / focus-visible / active / disabled / loading 五态**，用 `force-hover` /
 * `force-focus` / `force-active` 三个**强制类**把瞬时态固定下来（截图需要）。
 *
 * 强制类的实现有两套：
 * - 元素自带：`<Button className="force-hover">`（ui.css 里 `.ui-btn.force-hover`）；
 * - 祖先作用域：`<div className="force-hover"><Checkbox/></div>`（交互节点在内部时）。
 * 两套规则都写在 `ui/ui.css` 末尾，生产代码不会加这些类。
 */
import { useState, type ReactNode } from "react";
import {
  Bell,
  Download,
  ExternalLink,
  Pencil,
  Plus,
  Settings,
  Trash2,
} from "lucide-react";

import { CommandPalette } from "@/components/business/CommandPalette";
import { DurationLabel } from "@/components/business/DurationLabel";
import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import { MarkdownView } from "@/components/business/MarkdownView";
import { ResourceCard } from "@/components/business/ResourceCard";
import { StatusBadge } from "@/components/business/StatusBadge";
import { TypeIcon } from "@/components/business/TypeIcon";
import {
  Accordion,
  Avatar,
  Badge,
  Button,
  Card,
  Checkbox,
  Combobox,
  Dialog,
  DropdownMenu,
  IconButton,
  Input,
  Pagination,
  Panel,
  Popover,
  ProgressBar,
  ProgressRing,
  RadioGroup,
  Select,
  Separator,
  Sheet,
  Skeleton,
  Slider,
  Spinner,
  Switch,
  Tabs,
  Tag,
  Textarea,
  Toast,
  ToastStack,
  Tooltip,
  TooltipProvider,
} from "@/components/ui";
import type { TocNode } from "@/types/api";

/* -------------------------------------------------------------- 版式小件 */

function Section({
  id,
  title,
  component,
  note,
  children,
}: {
  id?: string;
  title: string;
  component?: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <section className="g-section" id={id}>
      <h2 className="g-section__title">
        {title}
        {component ? <code>{component}</code> : null}
      </h2>
      {note ? <p className="g-section__note">{note}</p> : null}
      <div className="g-section__body">{children}</div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="g-row">
      <div className="g-row__label">{label}</div>
      <div className="g-cluster">{children}</div>
    </div>
  );
}

function Cells({ children }: { children: ReactNode }) {
  return <div className="g-cells">{children}</div>;
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="g-cell">
      <div className="g-cell__label">{label}</div>
      <div className="g-cell__body">{children}</div>
    </div>
  );
}

/** 按钮五态的通用摆法：默认 / hover / focus / active / disabled / loading。 */
function ButtonStates({ variant }: { variant: "primary" | "secondary" | "ghost" | "danger" }) {
  return (
    <Cells>
      <Cell label="default">
        <Button variant={variant}>默认</Button>
      </Cell>
      <Cell label="hover">
        <Button variant={variant} className="force-hover">
          悬浮
        </Button>
      </Cell>
      <Cell label="focus-visible">
        <Button variant={variant} className="force-focus">
          聚焦
        </Button>
      </Cell>
      <Cell label="active">
        <Button variant={variant} className="force-active">
          按下
        </Button>
      </Cell>
      <Cell label="disabled">
        <Button variant={variant} disabled>
          禁用
        </Button>
      </Cell>
      <Cell label="loading">
        <Button variant={variant} loading>
          提交中
        </Button>
      </Cell>
    </Cells>
  );
}

/* ------------------------------------------------------------- 静态样例 */

const DEMO_TOC: TocNode[] = [
  { level: 2, text: "正常内容", anchor: "demo-normal" },
  { level: 3, text: "代码块", anchor: "demo-code" },
  { level: 2, text: "恶意 HTML 样例", anchor: "demo-evil" },
];

const NORMAL_MD = [
  "## 正常内容",
  "",
  "这是一段**正文**，行高 1.7。外链示例：[菜鸟教程](https://www.runoob.com)，",
  "行内代码 `state.value` 有浅绿底。",
  "",
  "> 引用块：左侧是琥珀色竖条，背景比正文略暖。",
  "",
  "### 代码块",
  "",
  "```python",
  "def fib(n: int) -> int:",
  "    a, b = 0, 1",
  "    for _ in range(n):",
  "        a, b = b, a + b",
  "    return a",
  "```",
  "",
  "| 算法 | 平均复杂度 | 稳定性 |",
  "| --- | --- | --- |",
  "| 快速排序 | O(n log n) | 不稳定 |",
  "| 归并排序 | O(n log n) | 稳定 |",
  "",
  "- [x] 已完成的条目",
  "- [ ] 未完成的条目",
  "",
  "![示例插图](/src/gallery/sample.svg)",
].join("\n");

const EVIL_MD = [
  "## 恶意 HTML 样例",
  "",
  "下面是原始 HTML，渲染结果里必须**完全不存在或已失效**（§13.2 第 1 条）：",
  "",
  "<script>alert('xss')</script>",
  "",
  "<iframe src=\"https://example.com\"></iframe>",
  "",
  "<img src=\"x\" onerror=\"alert('xss')\">",
  "",
  "<a href=\"javascript:alert('xss')\">点我执行脚本</a>",
  "",
  "<style>body { display: none }</style>",
  "",
  "**这一段必须照常可见**——sanitize 只剥危险节点，不吞正文。",
].join("\n");

const SELECT_OPTIONS = [
  { value: "beginner", label: "入门" },
  { value: "intermediate", label: "进阶" },
  { value: "advanced", label: "高级" },
  { value: "video", label: "视频（0 篇，禁用示例）", disabled: true },
];

const COMBO_OPTIONS = [
  { value: "frontend", label: "frontend", description: "5 类" },
  { value: "cs-basics", label: "cs-basics", description: "16 类" },
  { value: "backend", label: "backend", description: "4 类" },
  { value: "languages", label: "languages", description: "2 类" },
];

const PANEL_TABS = [
  { value: "preview", label: "预览", content: <p className="g-inline-note">预览内容：Tab 切换是本地状态，不触发请求。</p> },
  { value: "source", label: "源码", content: <p className="g-inline-note">源码内容（等宽字体由 markdown 的代码块负责）。</p> },
  { value: "disabled", label: "禁用项", content: <p className="g-inline-note">永远到不了这里。</p>, disabled: true },
];

/* ================================================================== 主体 */

export function Gallery() {
  const [level, setLevel] = useState("beginner");
  const [stack, setStack] = useState("cs-basics");
  const [checkbox, setCheckbox] = useState<boolean | "indeterminate">(true);
  const [radio, setRadio] = useState("medium");
  const [switched, setSwitched] = useState(true);
  const [sliderValue, setSliderValue] = useState(40);
  const [tags, setTags] = useState<string[]>(["闭包", "动态规划"]);
  const [page, setPage] = useState(3);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const allTags = ["闭包", "动态规划", "图论", "排序", "回溯"];

  return (
    <TooltipProvider>
      <div className="g-page">
        <header className="g-header">
          <h1>pgassistant 组件库画廊</h1>
          <p>
            仅开发态 <code>/gallery.html</code>，不进生产构建。每个交互组件都铺开
            hover / focus-visible / active / disabled / loading 五态；瞬时态用{" "}
            <code>force-hover</code> / <code>force-focus</code> / <code>force-active</code>{" "}
            强制类固定（生产不会出现这三个类）。颜色全部来自 tokens。
          </p>
        </header>

        {/* ------------------------------------------------------------ 总览 */}
        <Section
          title="总览"
          component="每类一个样例"
          note="先看一眼整体质感；每一类的变体与五态在下面的分段里。"
        >
          <Row label="常用件">
            <Button variant="primary" size="sm">
              主要按钮
            </Button>
            <Button variant="secondary" size="sm">
              次要按钮
            </Button>
            <IconButton label="通知" icon={<Bell size={16} />} />
            <div style={{ width: 160 }}>
              <Input placeholder="输入框" aria-label="输入框" />
            </div>
            <div style={{ width: 140 }}>
              <Select
                value="beginner"
                onValueChange={() => undefined}
                options={SELECT_OPTIONS}
                ariaLabel="难度"
              />
            </div>
            <Checkbox label="勾选" checked onCheckedChange={() => undefined} />
            <Switch checked onCheckedChange={() => undefined} ariaLabel="开关" />
            <Tag selected onClick={() => undefined}>
              标签
            </Tag>
            <Badge tone="accent">难度</Badge>
            <StatusBadge status="done" />
            <ProgressRing percent={56} size="sm" />
            <div style={{ width: 120 }}>
              <ProgressBar value={56} />
            </div>
            <Avatar name="张三" size="sm" />
            <Spinner />
            <DurationLabel minutes={45} />
          </Row>
        </Section>

        {/* ------------------------------------------------------------ 按钮 */}
        <Section
          title="Button"
          component="ui/Button"
          note="4 变体 × 3 尺寸；五态齐全（loading 时保留文案、用 Spinner 换掉左侧图标，宽度不跳）。"
        >
          <Row label="变体 × 尺寸">
            {(["primary", "secondary", "ghost", "danger"] as const).map((variant) => (
              <div className="g-variant" key={variant}>
                <Button variant={variant} size="sm">
                  sm
                </Button>
                <Button variant={variant} size="md">
                  md
                </Button>
                <Button variant={variant} size="lg">
                  lg
                </Button>
              </div>
            ))}
          </Row>
          <Row label="primary 五态">
            <ButtonStates variant="primary" />
          </Row>
          <Row label="secondary 五态">
            <ButtonStates variant="secondary" />
          </Row>
          <Row label="ghost 五态">
            <ButtonStates variant="ghost" />
          </Row>
          <Row label="danger 五态">
            <ButtonStates variant="danger" />
          </Row>
          <Row label="带图标 / block">
            <Button variant="primary" iconLeft={<Plus size={14} />}>
              加入计划
            </Button>
            <Button variant="secondary" iconRight={<Download size={14} />}>
              导出
            </Button>
            <div style={{ width: 220 }}>
              <Button variant="primary" block>
                整行按钮
              </Button>
            </div>
          </Row>
        </Section>

        <Section
          title="IconButton"
          component="ui/IconButton"
          note="`label` 是必填的 aria-label（§9.7）；五态同 Button。"
        >
          <Cells>
            <Cell label="default">
              <IconButton label="设置" icon={<Settings size={16} />} />
            </Cell>
            <Cell label="hover">
              <IconButton label="通知" icon={<Bell size={16} />} className="force-hover" />
            </Cell>
            <Cell label="focus">
              <IconButton label="编辑" icon={<Pencil size={16} />} className="force-focus" />
            </Cell>
            <Cell label="active">
              <IconButton label="删除" icon={<Trash2 size={16} />} className="force-active" />
            </Cell>
            <Cell label="disabled">
              <IconButton label="设置" icon={<Settings size={16} />} disabled />
            </Cell>
            <Cell label="loading">
              <IconButton label="加载中" icon={<Settings size={16} />} loading />
            </Cell>
          </Cells>
        </Section>

        {/* ------------------------------------------------------- 表单控件 */}
        <Section
          title="Input / Textarea"
          component="ui/Input · ui/Textarea"
          note="label 自动 for 关联、错误走 aria-invalid + aria-describedby；文本框没有独立的 active 态（按下即聚焦）。"
        >
          <div className="g-grid-2">
            <div className="g-stack">
              <Input label="关键词" placeholder="搜索资料…" hint="回车开始检索" />
              <Input label="带错误" defaultValue="kafka" error="没有匹配的资料" />
              <Input label="加载中" defaultValue="正在校验…" loading />
              <Input label="禁用" defaultValue="不可编辑" disabled />
            </div>
            <div className="g-stack">
              <Textarea label="计划目标" placeholder="例如：12 周内补齐前端基础" hint="1–200 字" rows={3} />
              <Textarea label="错误态" defaultValue="太短" error="至少 5 个字" rows={2} />
            </div>
          </div>
          <Row label="Input 五态（强制类）">
            <Cells>
              <Cell label="default">
                <Input placeholder="默认" aria-label="默认输入框" />
              </Cell>
              <Cell label="hover">
                <div className="force-hover">
                  <Input placeholder="悬浮" aria-label="悬浮输入框" />
                </div>
              </Cell>
              <Cell label="focus">
                <div className="force-focus">
                  <Input placeholder="聚焦" aria-label="聚焦输入框" />
                </div>
              </Cell>
              <Cell label="active">
                <div className="force-active">
                  <Input placeholder="按下" aria-label="按下输入框" />
                </div>
              </Cell>
              <Cell label="disabled">
                <Input placeholder="禁用" aria-label="禁用输入框" disabled />
              </Cell>
              <Cell label="loading">
                <Input placeholder="加载" aria-label="加载输入框" loading />
              </Cell>
            </Cells>
          </Row>
        </Section>

        <Section
          title="Select / Combobox"
          component="ui/Select · ui/Combobox"
          note="都是自绘/Radix 封装，没有原生 select；Combobox 支持 ↑↓ / Enter / Esc（Esc 只收下拉，不关外层弹层）。"
        >
          <div className="g-grid-2">
            <div className="g-stack">
              <Select
                label="难度"
                value={level}
                onValueChange={setLevel}
                options={SELECT_OPTIONS}
                hint="选项来自接口返回，不得硬编码（D19）"
              />
              <Select label="加载中" value={undefined} onValueChange={() => undefined} options={SELECT_OPTIONS} loading />
              <Select label="禁用" value="beginner" onValueChange={() => undefined} options={SELECT_OPTIONS} disabled />
            </div>
            <div className="g-stack">
              <Combobox
                label="技术栈"
                value={stack}
                onChange={setStack}
                options={COMBO_OPTIONS}
                hint="输入可过滤；↑↓ 选择，Enter 确认"
              />
              <Combobox
                label="无匹配示例"
                value={undefined}
                onChange={() => undefined}
                options={[]}
                emptyText="没有匹配的技术栈"
              />
            </div>
          </div>
        </Section>

        <Section
          title="Checkbox / Radio / Switch / Slider"
          component="ui/Checkbox · ui/Radio · ui/Switch · ui/Slider"
          note="全部 Radix 封装，无原生控件裸奔；下图每行左侧是可用态，右侧是 disabled。"
        >
          <div className="g-grid-2">
            <div className="g-stack">
              <Checkbox label="已选（可切换）" checked={checkbox} onCheckedChange={setCheckbox} />
              <Checkbox label="半选" checked="indeterminate" onCheckedChange={() => undefined} />
              <Checkbox label="加载中" checked onCheckedChange={() => undefined} loading />
              <Checkbox label="禁用" checked onCheckedChange={() => undefined} disabled />
              <div className="force-focus">
                <Checkbox label="focus-visible（强制）" checked={false} onCheckedChange={() => undefined} />
              </div>
            </div>

            <div className="g-stack">
              <RadioGroup
                label="每周投入"
                ariaLabel="每周投入"
                value={radio}
                onValueChange={setRadio}
                options={[
                  { value: "small", label: "5 小时", hint: "轻量" },
                  { value: "medium", label: "10 小时", hint: "推荐" },
                  { value: "large", label: "20 小时", hint: "高强度" },
                  { value: "never", label: "不可选", disabled: true },
                ]}
              />
              <Switch label="语义检索" hint="关闭后只看 FTS 结果" checked={switched} onCheckedChange={setSwitched} />
              <Switch label="禁用开关" checked={false} onCheckedChange={() => undefined} disabled />
              <Slider label="每周学时" value={sliderValue} onValueChange={(values) => setSliderValue(values[0] ?? 0)} max={20} />
              <div className="force-active">
                <Slider label="强制 active 滑块" value={12} onValueChange={() => undefined} max={20} />
              </div>
            </div>
          </div>
        </Section>

        {/* --------------------------------------------------- 标签与指示器 */}
        <Section
          title="Tag / Badge"
          component="ui/Tag · ui/Badge"
          note="Tag 可选中（aria-pressed）/可移除；Badge 是纯展示，四态不适用（文件头写了理由）。"
        >
          <Row label="可选中 Tag（点击切换）">
            {allTags.map((tag) => (
              <Tag
                key={tag}
                selected={tags.includes(tag)}
                onClick={() =>
                  setTags((current) =>
                    current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag],
                  )
                }
              >
                {tag}
              </Tag>
            ))}
          </Row>
          <Row label="Tag 五态">
            <Cells>
              <Cell label="default">
                <Tag onClick={() => undefined}>默认</Tag>
              </Cell>
              <Cell label="hover">
                <Tag className="force-hover" onClick={() => undefined}>
                  悬浮
                </Tag>
              </Cell>
              <Cell label="focus">
                <Tag className="force-focus" onClick={() => undefined}>
                  聚焦
                </Tag>
              </Cell>
              <Cell label="selected">
                <Tag selected onClick={() => undefined}>
                  已选
                </Tag>
              </Cell>
              <Cell label="disabled">
                <Tag disabled onClick={() => undefined}>
                  禁用
                </Tag>
              </Cell>
              <Cell label="loading">
                <Tag loading onClick={() => undefined}>
                  加载
                </Tag>
              </Cell>
              <Cell label="可移除">
                <Tag onRemove={() => undefined} removeLabel="移除标签">
                  移除
                </Tag>
              </Cell>
            </Cells>
          </Row>
          <Row label="Badge 色调 / 尺寸">
            <Badge>中性</Badge>
            <Badge tone="primary">主色</Badge>
            <Badge tone="accent">难度</Badge>
            <Badge tone="danger">失败</Badge>
            <Badge size="md" tone="primary">
              中号
            </Badge>
          </Row>
          <Row label="StatusBadge（业务）">
            <StatusBadge status="todo" />
            <StatusBadge status="done" />
            <StatusBadge status="skipped" />
            <StatusBadge status="active" />
            <StatusBadge status="archived" />
            <StatusBadge status="running" />
            <StatusBadge status="failed" />
          </Row>
        </Section>

        <Section
          title="Tooltip / Avatar / Separator"
          component="ui/Tooltip · ui/Avatar · ui/Separator"
          note="Tooltip 用于禁用按钮的原因说明（§9.6 Offline）；禁用按钮本身收不到 hover，所以外面套了一层可聚焦 span。"
        >
          <Row label="Tooltip">
            <Tooltip content="把这条资料加入当前计划">
              <Button variant="secondary" iconLeft={<Plus size={14} />}>
                悬浮看提示
              </Button>
            </Tooltip>
            <Tooltip content="该语言暂不支持站内运行">
              <span tabIndex={0} style={{ display: "inline-flex" }}>
                <Button variant="secondary" disabled>
                  运行（C++）
                </Button>
              </span>
            </Tooltip>
          </Row>
          <Row label="Avatar（首字块 / 图片 / 加载 / 尺寸）">
            <Avatar name="张三" />
            <Avatar name="Ada Lovelace" size="sm" />
            <Avatar name="李四" size="lg" />
            <Avatar name="示例插图" src="/src/gallery/sample.svg" size="md" />
            <Avatar name="加载中" loading />
          </Row>
          <Row label="Separator">
            <div style={{ display: "flex", alignItems: "center", gap: 12, width: 320 }}>
              <span>上</span>
              <Separator />
              <span>下</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, height: 24 }}>
              <span>左</span>
              <Separator orientation="vertical" />
              <span>右</span>
            </div>
          </Row>
        </Section>

        <Section
          title="Skeleton / Spinner / ProgressBar / ProgressRing"
          component="ui/Skeleton · ui/Spinner · ui/ProgressBar · ui/ProgressRing"
          note="加载三件套：骨架屏与真实布局同尺寸（§9.6）；不确定进度用 value=null 来回跑。"
        >
          <div className="g-grid-2">
            <div className="g-stack">
              <Skeleton shape="title" />
              <Skeleton shape="text" />
              <Skeleton shape="text" width="80%" />
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <Skeleton shape="circle" width={32} height={32} />
                <Skeleton width={120} height={32} />
              </div>
            </div>
            <div className="g-stack">
              <div className="g-cluster">
                <Spinner size="sm" />
                <Spinner />
                <Spinner size="lg" />
                <span className="g-inline-note">Spinner 无 hover/focus 态（纯装饰，读屏念 label）</span>
              </div>
              <ProgressBar value={32} label="计划进度" />
              <ProgressBar value={68} label="索引进度" tone="accent" />
              <ProgressBar value={null} label="正在扫描私有资料" />
              <ProgressBar value={100} label="已完成" tone="primary" />
              <div className="g-cluster">
                <ProgressRing percent={32} size="sm" />
                <ProgressRing percent={56} />
                <ProgressRing percent={78} size="lg" tone="accent" />
                <ProgressRing percent={100} label="✓" />
              </div>
            </div>
          </div>
        </Section>

        <Section
          title="Pagination"
          component="ui/Pagination"
          note="页码 + 省略号 + 上下页；当前页 aria-current=page；loading 时整组禁用防连点。"
        >
          <Pagination page={page} totalPages={14} onChange={setPage} />
          <Pagination page={1} totalPages={14} onChange={() => undefined} />
          <Pagination page={3} totalPages={3} onChange={() => undefined} />
          <Pagination page={5} totalPages={30} onChange={() => undefined} loading />
        </Section>

        {/* ------------------------------------------------------- 容器浮层 */}
        <Section
          title="Card / Panel"
          component="ui/Card · ui/Panel"
          note="1px 边框 + 背景色差表达层级，最多一档极轻阴影（§9.2 第 5 条）；hover 位移 2px。"
        >
          <div className="g-grid-2">
            <Card title="普通卡片" headerExtra={<Badge tone="primary">新</Badge>} footer={<Button size="sm">次要动作</Button>}>
              <p className="g-inline-note">Card 是布局容器：loading 态不适用，骨架用 Skeleton 组合。</p>
            </Card>
            <Card hoverable title="可悬浮卡片" className="force-hover">
              <p className="g-inline-note">这一张用了 force-hover：边框变绿、上浮 2px、出现阴影。</p>
            </Card>
          </div>
          <Panel
            title="Panel 标题"
            description="分组容器的标题栏 + 操作区 + 底栏"
            actions={
              <>
                <Button size="sm" variant="ghost">
                  取消
                </Button>
                <Button size="sm" variant="primary">
                  保存
                </Button>
              </>
            }
            footer={<span className="g-inline-note">底栏常放"上一次保存时间"或分页</span>}
          >
            <p className="g-inline-note">正文区域。`flush` 可去掉内边距，让列表/表格贴边。</p>
          </Panel>
        </Section>

        <Section
          title="Tabs / Accordion"
          component="ui/Tabs · ui/Accordion"
          note="两者都键盘可达（←→ / Enter / Home / End）；禁用项不参与 tab 序列。"
        >
          <div className="g-grid-2">
            <Tabs items={PANEL_TABS} ariaLabel="面板示例" />
            <Accordion
              defaultOpen={["stage-1"]}
              items={[
                { value: "stage-1", title: "阶段一 · 语言基础", content: "12 个条目，预计 180 分钟。" },
                { value: "stage-2", title: "阶段二 · 数据结构", content: "8 个条目，预计 240 分钟。" },
                { value: "stage-3", title: "阶段三 · 归档（禁用）", content: "不可展开。", disabled: true },
              ]}
            />
          </div>
        </Section>

        <Section
          title="Dialog / Sheet"
          component="ui/Dialog · ui/Sheet"
          note="焦点锁定、Esc 关闭、关闭后焦点回到触发元素（Radix Dialog 提供）。点按钮看真实交互。"
        >
          <Row label="点击打开">
            <Button variant="secondary" onClick={() => setDialogOpen(true)}>
              打开 Dialog
            </Button>
            <Button variant="secondary" onClick={() => setSheetOpen(true)}>
              打开 Sheet（移动端抽屉）
            </Button>
          </Row>
          <Dialog
            open={dialogOpen}
            onOpenChange={setDialogOpen}
            title="删除这个计划？"
            description="软删除会保留打勾历史，但计划不再出现在列表与侧栏。"
            footer={
              <>
                <Button variant="ghost" onClick={() => setDialogOpen(false)}>
                  取消
                </Button>
                <Button variant="danger" onClick={() => setDialogOpen(false)}>
                  删除
                </Button>
              </>
            }
          >
            <p className="g-inline-note">对话框正文。焦点被锁在这里，Tab 不会跑到背景。</p>
          </Dialog>
          <Sheet open={sheetOpen} onOpenChange={setSheetOpen} title="运行面板（移动端）">
            <p className="g-inline-note">Sheet 与 Dialog 同一套焦点契约，只是从右侧贴边滑出。</p>
            <div style={{ marginTop: 16 }}>
              <Button variant="primary" block>
                运行代码
              </Button>
            </div>
          </Sheet>
        </Section>

        <Section
          title="DropdownMenu / Popover"
          component="ui/DropdownMenu · ui/Popover"
          note="菜单项有 hover/active/focus/disabled/loading 五态；下面左二直接用强制类把菜单内容摊开（真实菜单点按钮才出现）。"
        >
          <Row label="点开菜单 / 点开浮层">
            <DropdownMenu
              trigger={
                <Button variant="secondary" iconRight={<Download size={14} />}>
                  计划操作
                </Button>
              }
              ariaLabel="计划操作"
              entries={[
                { kind: "label", key: "l", label: "计划" },
                { kind: "item", key: "rename", label: "重命名", icon: <Pencil size={14} /> },
                { kind: "checkbox", key: "archive", label: "归档后隐藏进度", checked: true, onCheckedChange: () => undefined },
                { kind: "separator", key: "s" },
                { kind: "item", key: "loading", label: "正在导出…", loading: true },
                { kind: "item", key: "delete", label: "删除", danger: true, icon: <Trash2 size={14} /> },
              ]}
            />
            <Popover
              ariaLabel="关于进度"
              trigger={<Button variant="ghost">浮层说明</Button>}
            >
              <p className="g-inline-note">Popover 内容：进度按学时加权（§4.6）。</p>
            </Popover>
          </Row>
          <Row label="菜单项五态（强制类摊开）">
            <div className="ui-menu__content" style={{ position: "static" }}>
              <div className="ui-menu__item">默认项</div>
              <div className="ui-menu__item force-hover">hover 项</div>
              <div className="ui-menu__item force-active">active 项</div>
              <div className="ui-menu__item force-focus">focus 项</div>
              <div className="ui-menu__item" data-disabled="true">
                禁用项
              </div>
            </div>
          </Row>
        </Section>

        <Section
          title="Toast"
          component="ui/Toast"
          note="只画一条消息，队列在 lib/query.ts（ToastHost 负责映射）；disabled / loading 不适用（Toast 不是可点目标）。"
        >
          <ToastStack>
            <Toast kind="success" message="计划已保存" hint="已在侧栏切换为激活计划" onDismiss={() => undefined} />
            <Toast kind="info" message="索引重建中，结果可能偏旧" onDismiss={() => undefined} />
            <Toast kind="warn" message="语义检索暂不可用" hint="当前只返回 FTS 结果" onDismiss={() => undefined} />
            <Toast kind="error" message="连不上本地服务" hint="确认服务还在运行后重试" onDismiss={() => undefined} />
          </ToastStack>
        </Section>

        {/* --------------------------------------------------------- 业务件 */}
        <Section
          id="resource-card"
          title="ResourceCard"
          component="business/ResourceCard"
          note="封面 16:9 或首字块 + 标题两行截断 + 摘要两行 + 标签 + 类型/时长 + 难度徽章；hover 浮现「加入计划」。第二张用强制 hover 展示。"
        >
          <div className="g-grid-3">
            <ResourceCard
              title="JavaScript 闭包：作用域与词法环境"
              summary="闭包是一种保护私有变量的机制：函数执行时创建私有作用域，外部只能通过返回的函数访问。"
              href="/library/frontend/javascript/js-closures"
              coverUrl="/src/gallery/sample.svg"
              tags={["闭包", "作用域", "词法环境", "进阶"]}
              durationMin={5}
              level="beginner"
              type="docs"
              onAddToPlan={() => undefined}
            />
            <ResourceCard
              title="动态规划解题框架（无封面，用首字块）"
              summary="从暴力递归到记忆化再到递推，三步走。"
              href="/library/cs-basics/dynamic-programming/dp_solution_pipeline"
              tags={["动态规划"]}
              durationMin={90}
              level="intermediate"
              type="article"
              forceHover
              onAddToPlan={() => undefined}
              addToPlanLoading
            />
            <ResourceCard
              title="我的私有笔记：图论复习"
              summary="私有资料带角标；来源是 data/private/，与仓库内容物理隔离。"
              href="/library/private/graph-notes"
              tags={["图论", "私有"]}
              durationMin={null}
              level="advanced"
              type="summary"
              source="private"
              onAddToPlan={() => undefined}
            />
          </div>
        </Section>

        <Section
          id="markdown"
          title="MarkdownView"
          component="business/MarkdownView"
          note="react-markdown + remark-gfm + rehype-highlight + rehype-sanitize（必须）；标题 id 按 toc 顺序注入，下面的目录链接就是验证。"
        >
          <div className="g-toc">
            <span className="g-inline-note">toc 锚点：</span>
            {DEMO_TOC.map((node) => (
              <a key={node.anchor} href={`#${node.anchor}`}>
                {node.text}（h{node.level} → #{node.anchor}）
              </a>
            ))}
          </div>
          <MarkdownView markdown={NORMAL_MD} toc={DEMO_TOC} />
          <Separator />
          <MarkdownView markdown={EVIL_MD} toc={DEMO_TOC} />
        </Section>

        <Section
          id="states"
          title="EmptyState / ErrorState"
          component="business/EmptyState · business/ErrorState"
          note="空状态是自绘 SVG（不引第三方图）+ 一句说明 + 一个主行动；错误态给错误码 + 人话 + 重试 + 复制详情。"
        >
          <div className="g-grid-2">
            <EmptyState
              title="还没有学习计划"
              description="让助手用一句话生成一份，或手动从资料库挑选。"
              illustration="plans"
              action={<Button variant="primary">让助手生成计划</Button>}
            />
            <EmptyState
              title="没有匹配的资料"
              description="去掉筛选条件，或换个关键词。"
              illustration="search"
              compact
              action={<Button variant="secondary">去掉筛选</Button>}
            />
          </div>
          <div className="g-grid-2">
            <ErrorState code="RESOURCE_NOT_FOUND" onRetry={() => undefined} />
            <ErrorState
              code="NETWORK_ERROR"
              hint="确认服务还在运行；查询类请求会自动重试 1 次"
              onRetry={() => undefined}
              retrying
            />
            <ErrorState
              code="INTERNAL"
              message="出了点问题"
              requestId="req_01HZX8K3"
              detail={{ trace: "Traceback (most recent call last): ..." }}
              onRetry={() => undefined}
              showDetail
            />
            <ErrorState code="SOMETHING_NEW" message="未登记的错误码走 UNKNOWN 兜底" />
          </div>
        </Section>

        <Section
          title="TypeIcon / DurationLabel"
          component="business/TypeIcon · business/DurationLabel"
          note="8 值类型映射只决定图标与文案，不当筛选器选项来源（D19）；时长口径见 §4.6。"
        >
          <Row label="8 种类型">
            <TypeIcon type="article" showLabel />
            <TypeIcon type="docs" showLabel />
            <TypeIcon type="exercise" showLabel />
            <TypeIcon type="overview" showLabel />
            <TypeIcon type="summary" showLabel />
            <TypeIcon type="video" showLabel />
            <TypeIcon type="course" showLabel />
            <TypeIcon type="book" showLabel />
            <TypeIcon type={null} showLabel />
          </Row>
          <Row label="时长格式">
            <DurationLabel minutes={5} />
            <DurationLabel minutes={45} />
            <DurationLabel minutes={60} />
            <DurationLabel minutes={90} />
            <DurationLabel minutes={null} />
          </Row>
        </Section>

        <Section
          id="palette"
          title="CommandPalette"
          component="business/CommandPalette"
          note="静态展示复用 .cp 样式（不触发检索）。真实面板：Ctrl/⌘+K 打开，输入后调 POST /api/search，接口未实现时展示错误态而不是崩。"
        >
          <Row label="静态展示（空态 / 结果态）">
            <div className="g-preview cp">
              <div className="cp__search">
                <span className="cp__search-icon">
                  <span aria-hidden="true">⌕</span>
                </span>
                <input className="cp__input" value="闭包" readOnly aria-label="静态展示搜索框" />
                <kbd className="cp__kbd">Esc</kbd>
              </div>
              <div className="cp__body">
                <ul className="cp__list" role="list">
                  <li>
                    <div className="cp__hit" aria-selected="true">
                      <span className="cp__hit-title">JavaScript 闭包：作用域与词法环境</span>
                      <span className="cp__hit-snippet">
                        …<mark className="cp__mark">闭包</mark>是一种保护私有变量的机制，它在函数执行时创建一个私有作用域…
                      </span>
                      <span className="cp__hit-meta">frontend / javascript</span>
                    </div>
                  </li>
                  <li>
                    <div className="cp__hit">
                      <span className="cp__hit-title">JS 作用域链与闭包的常见坑</span>
                      <span className="cp__hit-snippet">循环里的 <mark className="cp__mark">闭包</mark>会共享同一个变量…</span>
                      <span className="cp__hit-meta">frontend / javascript</span>
                    </div>
                  </li>
                </ul>
              </div>
              <div className="cp__footer">
                <span>↵ Enter 打开</span>
                <span>↑ ↓ 选择</span>
                <span className="cp__footer-end">2 条结果</span>
              </div>
            </div>

            <div className="g-preview cp">
              <div className="cp__search">
                <span className="cp__search-icon">
                  <span aria-hidden="true">⌕</span>
                </span>
                <input className="cp__input" value="kafka" readOnly aria-label="静态展示搜索框（零结果）" />
                <kbd className="cp__kbd">Esc</kbd>
              </div>
              <div className="cp__body">
                <div className="cp__empty">
                  <p>没有匹配「kafka」的资料</p>
                  <Button size="sm" variant="secondary">
                    去资料库搜索
                  </Button>
                </div>
              </div>
              <div className="cp__footer">
                <span>⌘K 开关面板</span>
                <span className="cp__footer-end">0 条结果</span>
              </div>
            </div>
          </Row>

          <Row label="真实面板（会调接口；后端没起时展示错误态）">
            <Button variant="primary" onClick={() => setPaletteOpen(true)}>
              打开命令面板
            </Button>
            <span className="g-inline-note">
              也可以直接按 Ctrl/⌘+K —— 但画廊不是 AppShell，这个绑定只在本页的按钮上，
              生产里由 <code>AppShell</code> 的 <code>useHotkeys</code> 负责。
            </span>
          </Row>

          <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
        </Section>

        <footer className="g-header" style={{ borderTop: "1px solid var(--border)", borderBottom: 0, marginTop: 0 }}>
          <p>
            组件库交付物：<code>components/ui/**</code>（29 个）、<code>components/business/**</code>（8 个）、
            布局接入两处（CommandPalette / ToastHost）。截图产物在{" "}
            <code>handoff/team/artifacts/ui-1280.png</code> 与 <code>ui-375.png</code>。
          </p>
          <p className="g-inline-note">
            <ExternalLink size={11} aria-hidden="true" /> 本页不依赖后端，也不改任何生产配置。
          </p>
        </footer>
      </div>
    </TooltipProvider>
  );
}
