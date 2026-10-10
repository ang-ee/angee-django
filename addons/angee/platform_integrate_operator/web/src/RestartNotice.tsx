import { useAuthoredQuery } from "@angee/refine";
import { PendingAddonChanges, PLATFORM_ADDON_MUTATION_INVALIDATES } from "@angee/platform";
import {
  useJobRunOperation,
  useOperatorConnection,
} from "@angee/operator/runtime";
import { Banner, Button, NavLink, useRouteHref } from "@angee/ui";
import { useChromePlace } from "@angee/ui/chrome/refine-menu";
import { useEffect, type ReactNode } from "react";
import { usePlatformIntegrateOperatorT } from "./i18n";

/** Settings-only restart state, backed entirely by the daemon's durable receipt. */
export function RestartNotice(): ReactNode {
  const t = usePlatformIntegrateOperatorT();
  // The console's one place answer: a page renders in Settings by its route's anchor, not its URL alone.
  const visible = useChromePlace().railPlace.scope === "settings";
  const routeHref = useRouteHref();
  const operationsHref = routeHref("operator.operations");
  const pending = useAuthoredQuery(PendingAddonChanges, undefined, {
    enabled: visible,
    models: PLATFORM_ADDON_MUTATION_INVALIDATES,
  });
  const connection = useOperatorConnection();
  const run = useJobRunOperation({ enabled: visible });
  const operation = run.operation;
  const restartJob = connection?.restartJob;
  const applicationRestart = operation && restartJob
    && operation.rootJob === restartJob && operation.chainedRestart
    ? operation
    : null;
  useEffect(() => {
    if (visible && applicationRestart?.status === "SUCCEEDED") void pending.refetch();
  }, [applicationRestart?.id, applicationRestart?.status, pending.refetch, visible]);
  const running = applicationRestart?.status === "PENDING" || applicationRestart?.status === "RUNNING";
  const failed = applicationRestart?.status === "FAILED" || applicationRestart?.status === "BLOCKED";
  const restart = restartJob ? () => void run.run(restartJob, true) : undefined;

  if (!visible) return null;

  if (running) {
    return (
      <Banner
        tone="info"
        title={t("restart.running.title")}
        actions={<NavLink href={operationsHref} variant="inline">{t("restart.logs")}</NavLink>}
      >
        {applicationRestart?.currentStep ?? t("restart.running.waiting")}
      </Banner>
    );
  }
  if (run.startError) {
    return (
      <Banner
        tone="danger"
        title={t("restart.startFailed.title")}
        actions={restart ? <Button disabled={run.starting} size="sm" variant="secondary" onClick={restart}>{t("restart.retry")}</Button> : undefined}
      >
        {run.startError.message}
      </Banner>
    );
  }
  if (failed) {
    return (
      <Banner
        tone="danger"
        title={t("restart.failed.title")}
        actions={<><NavLink href={operationsHref} variant="inline">{t("restart.logs")}</NavLink>{restart ? <Button disabled={run.active || run.starting} size="sm" variant="secondary" onClick={restart}>{t("restart.retry")}</Button> : null}</>}
      >
        {applicationRestart?.error
          ?? applicationRestart?.nodes.find((node) => node.status === "FAILED")?.message
          ?? applicationRestart?.currentStep
          ?? t("restart.failed.fallback")}
      </Banner>
    );
  }
  if (run.queryError) {
    return (
      <Banner tone="warning" title={t("restart.statusUnavailable.title")}>
        {run.queryError.message}
      </Banner>
    );
  }
  if (pending.isFetching && !pending.data) return null;
  const explorer = pending.data?.platform_explorer;
  if (!pending.error && explorer == null) return null;
  if (pending.error || explorer?.pending_addon_changes == null) {
    return <Banner tone="warning" title={t("restart.statusUnavailable.title")}>{pending.error?.message ?? t("restart.statusUnavailable.description")}</Banner>;
  }
  if (explorer.pending_addon_changes) {
    return (
      <Banner
        tone="warning"
        title={t("restart.required.title")}
        actions={restart ? <Button disabled={run.active || run.starting} size="sm" variant="secondary" onClick={restart}>{t("restart.action")}</Button> : undefined}
      >
        {t("restart.required.description")}
      </Banner>
    );
  }
  return null;
}
