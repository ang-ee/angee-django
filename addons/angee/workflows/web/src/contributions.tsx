import { useDecision } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import type { ReactNode } from "react";
import { ErrorBanner, MetaSection, NavLink, useRouteHref, type ContainerChild } from "@angee/ui";

import { DecisionWaitingRunsDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";


export function DecisionRunOrigin() {
  const { decision } = useDecision();
  const t = useWorkflowsT();
  const href = useRouteHref();
  const decisionId = decision.id;
  const query = useAuthoredQuery(DecisionWaitingRunsDocument, { decision: decisionId }, {
    models: ["decisions.Decision", "workflows.StepRun"],
    records: [{ model: "decisions.Decision", id: decisionId }],
    relatedModels: ["workflows.StepRun"],
  });
  if (query.isLoading) return null;
  if (query.error) return <ErrorBanner description={t("catalogue.originUnavailable")} />;
  const steps = query.data?.steprun.flatMap((step) => step.run ? [{ ...step, run: step.run }] : []) ?? [];
  if (!steps.length) return null;
  return <MetaSection headingLevel={2} title={t("catalogue.waitingRuns")}><ul className="space-y-2">{steps.map((step) =>
    <li key={step.id}><NavLink href={href("workflows.runs.record", { id: step.run.id })} variant="inline">
      {step.run.display_name}</NavLink>{" · "}{step.node_label}{step.is_mapped ? ` [${step.map_index}]` : ""}</li>)}</ul></MetaSection>;
}

/** The run a decision waits in, as a `decisions#origin` child. */
export const decisionRunOrigin: ContainerChild<ReactNode> = { content: <DecisionRunOrigin /> };
