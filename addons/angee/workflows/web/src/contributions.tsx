import { useDecisionContent } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { lazy, type ReactNode } from "react";
import { ErrorBanner, LazyBoundary, LoadingPanel, MetaSection, TextLink, useRouteHref, type ChatterTabContent, type ContainerChild } from "@angee/ui";

import { DecisionWaitingRunsDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";

const RunsList = lazy(() => import("./RunsPage").then(({ RunsList }) => ({ default: RunsList })));

export function DecisionRunOrigin() {
  const { decision } = useDecisionContent();
  const t = useWorkflowsT();
  const href = useRouteHref();
  const groupId = decision.group?.id;
  const query = useAuthoredQuery(DecisionWaitingRunsDocument, { group: groupId ?? "" }, {
    enabled: groupId != null,
    models: ["decisions.DecisionGroup", "workflows.StepRun"],
    records: groupId != null ? [{ model: "decisions.DecisionGroup", id: groupId }] : [],
    relatedModels: ["workflows.StepRun"],
  });
  if (groupId == null) return null;
  if (query.isLoading) return null;
  if (query.error) return <ErrorBanner description={t("catalogue.originUnavailable")} />;
  const steps = query.data?.steprun.flatMap((step) => step.run ? [{ ...step, run: step.run }] : []) ?? [];
  if (!steps.length) return null;
  return <MetaSection headingLevel={2} title={t("catalogue.waitingRuns")}><ul className="space-y-2">{steps.map((step) =>
    <li key={step.id}><TextLink href={href("workflows.runs.record", { id: step.run.id })}>
      {step.run.display_name}</TextLink>{" · "}{step.node_label}{step.is_mapped ? ` [${step.map_index}]` : ""}</li>)}</ul></MetaSection>;
}

function RunsTabLabel() { return useWorkflowsT()("runs.title"); }

/** The shell owns record selection and tab lifetime; the runs owner owns the collection. */
export const workflowsRunsTab: ContainerChild<ChatterTabContent> = {
  sequence: 40,
  content: {
    icon: "versions", label: <RunsTabLabel />, aliases: ["workflows"],
    when: ({ view, route }) => view.kind === "record" && Boolean(view.sqid && route?.canonicalLabel) && !route?.canonicalLabel?.startsWith("workflows."),
    render: ({ view, route }) => view.sqid && route?.canonicalLabel ? <LazyBoundary pending={<LoadingPanel />}><RunsList embedded baseFilter={{
      subject_model: { exact: route.canonicalLabel }, subject_id: { exact: view.sqid },
    }} /></LazyBoundary> : null,
  },
};

/** The run a decision waits in, as a `decisions#origin` child. */
export const decisionRunOrigin: ContainerChild<ReactNode> = { content: <DecisionRunOrigin /> };
