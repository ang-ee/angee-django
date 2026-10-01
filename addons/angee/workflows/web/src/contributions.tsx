import { DECISION_ORIGIN_SLOT, useDecisionContent } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { lazy } from "react";
import { ErrorBanner, LazyBoundary, LoadingPanel, MetaSection, TextLink, useRouteHref, type ChatterContribution, type SlotContribution } from "@angee/ui";

import { DecisionWaitingRunsDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";

const RunsList = lazy(() => import("./RunsPage").then(({ RunsList }) => ({ default: RunsList })));

export function DecisionRunOrigin() {
  const { decision } = useDecisionContent();
  const t = useWorkflowsT();
  const href = useRouteHref();
  const query = useAuthoredQuery(DecisionWaitingRunsDocument, { group: decision.group.id }, {
    models: ["decisions.DecisionGroup", "workflows.StepRun"],
    records: [{ model: "decisions.DecisionGroup", id: decision.group.id }], relatedModels: ["workflows.StepRun"],
  });
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
export const workflowsChatter: ChatterContribution = {
  id: "workflows", sequence: 40, icon: "versions", label: <RunsTabLabel />,
  when: ({ view, route }) => view.kind === "record" && Boolean(view.sqid && route?.canonicalLabel) && !route?.canonicalLabel?.startsWith("workflows."),
  render: ({ view, route }) => view.sqid && route?.canonicalLabel ? <LazyBoundary pending={<LoadingPanel />}><RunsList embedded baseFilter={{
    subject_model: { exact: route.canonicalLabel }, subject_id: { exact: view.sqid },
  }} /></LazyBoundary> : null,
};

export const decisionRunOrigin: SlotContribution = {
  slot: DECISION_ORIGIN_SLOT, id: "workflows.run", content: <DecisionRunOrigin />,
};
