/**
 * 首页 —— `/`（Owner：资料页 M2；需求文档 §9.5 首页 / §9.9 首次引导）。
 *
 * 四块：
 * 1. 大搜索框（回车 → `/library?q=`，旁边提示 Ctrl/⌘+K 打开命令面板）；
 * 2. 首次引导带（`localStorage.onboarded_v1`，点任意按钮或关闭即写，永不再现）；
 * 3. 三个方向卡片（前端 / 后端 / 计算机基础，条目数来自 `GET /api/taxonomy`，不写死）；
 * 4. 「继续学习」：激活计划里最近 3 条未完成条目（`GET /api/plans` + `GET /api/plans/{id}`）。
 *
 * 三态：骨架（与真实卡片同高）、空（没有激活计划 → 引导去生成）、错误（ErrorState + 重试）。
 */
import { ArrowRight, Search as SearchIcon, Sparkles, X } from "lucide-react";
import { type FormEvent, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import { DurationLabel } from "@/components/business/DurationLabel";
import { TypeIcon } from "@/components/business/TypeIcon";
import { Button, Card, IconButton, Input, Skeleton } from "@/components/ui";
import type { ItemRead, StackNode } from "@/types/api";

import {
  useActivePlan,
  useOnboarded,
  usePlanDetail,
  useTaxonomyData,
} from "@/features/library/hooks";
import { libraryHref, resourceHref } from "@/features/library/media";

import "./home.css";

/** 首屏优先展示的方向；库里没有这个 stack 时按条目数补齐（不写死三个名字）。 */
const PREFERRED_STACKS = ["frontend", "backend", "cs-basics"];
const CONTINUE_LIMIT = 3;

interface PendingItem {
  item: ItemRead;
  stageTitle: string;
}

export default function HomePage() {
  const { t } = useTranslation("library");
  const navigate = useNavigate();
  const taxonomy = useTaxonomyData();
  const plans = useActivePlan();
  const activePlanId = plans.data?.active_plan_id ?? null;
  const plan = usePlanDetail(activePlanId);
  const onboarded = useOnboarded();

  const [text, setText] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const directions = useMemo<StackNode[]>(() => {
    const stacks = [...(taxonomy.data?.stacks ?? [])];
    const picked: StackNode[] = [];
    for (const name of PREFERRED_STACKS) {
      const found = stacks.find((node) => node.stack === name);
      if (found) picked.push(found);
    }
    for (const node of stacks) {
      if (picked.length >= 3) break;
      if (!picked.includes(node)) picked.push(node);
    }
    return picked.slice(0, 3);
  }, [taxonomy.data]);

  const pending = useMemo<PendingItem[]>(() => {
    if (!plan.data) return [];
    const rows: PendingItem[] = [];
    for (const stage of plan.data.stages) {
      for (const item of stage.items) {
        if (item.status === "done") continue;
        rows.push({ item, stageTitle: stage.title });
      }
    }
    return rows.slice(0, CONTINUE_LIMIT);
  }, [plan.data]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const query = text.trim();
    navigate(libraryHref(query ? { q: query } : {}));
  };

  const planFailure = plans.error;

  return (
    <section className="page-enter home">
      <header className="home__hero">
        <h1 className="home__title">{t("home.title")}</h1>
        <p className="home__subtitle">{t("home.subtitle")}</p>

        <form className="home__search" onSubmit={submit} role="search">
          <Input
            ref={searchRef}
            aria-label={t("home.searchLabel")}
            placeholder={t("home.searchPlaceholder")}
            value={text}
            onChange={(event) => setText(event.target.value)}
            iconLeft={<SearchIcon size={16} />}
            size="lg"
            fieldClassName="home__search-field"
          />
          <Button type="submit" size="lg">
            {t("home.searchAction")}
          </Button>
        </form>
        <p className="home__hint">{t("home.hotkey")}</p>
      </header>

      {!onboarded.seen ? (
        <aside className="home__onboard" aria-label={t("onboard.label")}>
          <p className="home__onboard-text">{t("onboard.text")}</p>
          <div className="home__onboard-actions">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                onboarded.dismiss();
                searchRef.current?.focus();
              }}
            >
              {t("onboard.search")}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                onboarded.dismiss();
                navigate("/plans/new");
              }}
            >
              {t("onboard.plan")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                onboarded.dismiss();
                navigate("/library");
              }}
            >
              {t("onboard.browse")}
            </Button>
          </div>
          <IconButton
            label={t("onboard.dismiss")}
            icon={<X size={14} />}
            onClick={onboarded.dismiss}
            className="home__onboard-close"
          />
        </aside>
      ) : null}

      <section className="home__section" aria-labelledby="home-directions">
        <h2 className="home__section-title" id="home-directions">
          {t("home.directions")}
        </h2>
        {taxonomy.isLoading ? (
          <ul className="home__cards" aria-busy="true">
            {[0, 1, 2].map((index) => (
              <li key={index}>
                <Skeleton height={112} />
              </li>
            ))}
          </ul>
        ) : taxonomy.error ? (
          <ErrorState
            code={taxonomy.error.code}
            message={taxonomy.error.message}
            hint={taxonomy.error.hint}
            onRetry={() => void taxonomy.refetch()}
            retrying={taxonomy.isFetching}
          />
        ) : (
          <ul className="home__cards">
            {directions.map((node) => (
              <li key={node.stack}>
                <Link className="home__card-link" to={libraryHref({ stack: node.stack })}>
                  <Card hoverable className="home__card">
                    <span className="home__card-title">{node.label?.zh ?? node.stack}</span>
                    <span className="home__card-count">
                      {t("home.count", { total: node.total })}
                    </span>
                    <span className="home__card-cta">
                      {t("home.enter")} <ArrowRight size={13} />
                    </span>
                  </Card>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="home__section" aria-labelledby="home-continue">
        <h2 className="home__section-title" id="home-continue">
          {t("home.continue")}
        </h2>

        {plans.isLoading || (activePlanId !== null && plan.isLoading) ? (
          <ul className="home__continue" aria-busy="true">
            {[0, 1, 2].map((index) => (
              <li key={index}>
                <Skeleton height={56} />
              </li>
            ))}
          </ul>
        ) : planFailure ? (
          <ErrorState
            code={planFailure.code}
            message={planFailure.message}
            hint={planFailure.hint}
            onRetry={() => void plans.refetch()}
            retrying={plans.isFetching}
          />
        ) : activePlanId === null ? (
          <EmptyState
            compact
            illustration="plans"
            title={t("home.noPlan.title")}
            description={t("home.noPlan.description")}
            action={
              <Button
                size="sm"
                iconLeft={<Sparkles size={14} />}
                onClick={() => navigate("/plans/new")}
              >
                {t("home.noPlan.action")}
              </Button>
            }
          />
        ) : pending.length === 0 ? (
          <EmptyState
            compact
            illustration="plans"
            title={t("home.allDone.title")}
            description={t("home.allDone.description")}
            action={
              <Button size="sm" variant="secondary" onClick={() => navigate(`/plans/${activePlanId}`)}>
                {t("home.allDone.action")}
              </Button>
            }
          />
        ) : (
          <>
            <p className="home__continue-plan">
              <Link to={`/plans/${activePlanId}`}>{plan.data?.title}</Link>
              {plan.data?.progress ? (
                <span className="tnum home__continue-progress">
                  {t("home.progress", { percent: Math.round(plan.data.progress.percent) })}
                </span>
              ) : null}
            </p>
            <ul className="home__continue">
              {pending.map(({ item, stageTitle }) => {
                const href = item.resource_slug
                  ? resourceHref(item.resource_slug)
                  : `/plans/${activePlanId}`;
                return (
                  <li key={item.id}>
                    <Link className="home__continue-row" to={href}>
                      <span className="home__continue-main">
                        <span className="home__continue-title">
                          {item.resource_available || !item.resource_slug
                            ? item.title
                            : (item.resolved_title ?? item.title)}
                        </span>
                        <span className="home__continue-stage">{stageTitle}</span>
                      </span>
                      <span className="home__continue-meta">
                        <TypeIcon type={null} />
                        <DurationLabel minutes={item.est_minutes} />
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </section>
    </section>
  );
}
