/**
 * 「加入计划」弹窗（Owner：资料页 M2）。
 *
 * 为什么不是"点一下就加"：`POST /api/plan-items` 的 body 必须带 `stage_id` 与
 * `version`（`types/api.ts::ItemCreate`，D4 乐观锁），资料本身不知道往哪个阶段放。
 * 所以这里列出**激活计划的阶段**让用户选一个，再把资料作为条目挂进去。
 *
 * 三种状态：
 * - 没有激活计划 → 引导去 `/plans/new`（不报错、不静默失败）；
 * - 计划树加载中 → Skeleton 行（与真实行同高，不跳动）；
 * - 409 `VERSION_CONFLICT` → 提示"计划已在别处更新"，并刷新 `["plan", id]` 后让用户重试。
 *
 * 只做展示与一次 mutation，缓存失效规则在 `hooks.ts::useAddToPlan` 里统一处理。
 */
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CalendarPlus } from "lucide-react";

import { EmptyState } from "@/components/business/EmptyState";
import { ErrorState } from "@/components/business/ErrorState";
import { Button, Dialog, Skeleton } from "@/components/ui";
import { useToast } from "@/lib/query";
import type { ApiError } from "@/lib/http";

import { useActivePlan, useAddToPlan, usePlanDetail } from "./hooks";

export interface AddToPlanDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 资料 slug 与标题快照（条目保存 `title` 快照，§8.4）。 */
  slug: string;
  title: string;
}

export function AddToPlanDialog({ open, onOpenChange, slug, title }: AddToPlanDialogProps) {
  const { t } = useTranslation("library");
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();

  const plans = useActivePlan();
  const activePlanId = plans.data?.active_plan_id ?? null;
  const plan = usePlanDetail(activePlanId);
  const addToPlan = useAddToPlan();

  const loading = plans.isLoading || (activePlanId !== null && plan.isLoading);
  const failure = (plans.error ?? plan.error) as ApiError | null;

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("addToPlan.title")}
      description={t("addToPlan.description")}
      width={520}
    >
      {loading ? (
        <div className="add-plan__list" aria-busy="true">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} height={52} />
          ))}
        </div>
      ) : failure ? (
        <ErrorState
          code={failure.code}
          hint={failure.hint}
          requestId={failure.requestId ?? null}
          onRetry={() => {
            void plans.refetch();
            if (activePlanId !== null) void plan.refetch();
          }}
          retrying={plans.isFetching || plan.isFetching}
        />
      ) : activePlanId === null ? (
        <EmptyState
          compact
          illustration="plans"
          title={t("addToPlan.noPlan.title")}
          description={t("addToPlan.noPlan.description")}
          action={
            <Button
              size="sm"
              iconLeft={<CalendarPlus size={14} />}
              onClick={() => {
                onOpenChange(false);
                navigate("/plans/new");
              }}
            >
              {t("addToPlan.noPlan.action")}
            </Button>
          }
        />
      ) : (
        <>
          <p className="add-plan__plan">{plan.data?.title}</p>
          <ul className="add-plan__list">
            {(plan.data?.stages ?? []).map((stage) => (
              <li key={stage.id}>
                <Button
                  variant="secondary"
                  className="add-plan__stage"
                  loading={addToPlan.isPending && addToPlan.variables?.stageId === stage.id}
                  disabled={addToPlan.isPending}
                  onClick={() => {
                    addToPlan.mutate(
                      {
                        planId: activePlanId,
                        stageId: stage.id,
                        version: plan.data?.version ?? 1,
                        slug,
                        title,
                      },
                      {
                        onSuccess: () => {
                          toast.push({
                            kind: "success",
                            message: t("addToPlan.success", { stage: stage.title }),
                          });
                          onOpenChange(false);
                        },
                        onError: (error) => {
                          toast.push({
                            kind: "error",
                            message: t("addToPlan.failed"),
                            hint: error.hint ?? error.message,
                          });
                          if (error.code === "VERSION_CONFLICT") {
                            void queryClient.invalidateQueries({
                              queryKey: ["plan", activePlanId],
                            });
                          }
                        },
                      },
                    );
                  }}
                >
                  <span className="add-plan__stage-title">{stage.title}</span>
                  <span className="add-plan__stage-meta">
                    {t("addToPlan.stageMeta", { count: stage.items.length, weeks: stage.weeks })}
                  </span>
                </Button>
              </li>
            ))}
          </ul>
        </>
      )}
    </Dialog>
  );
}
