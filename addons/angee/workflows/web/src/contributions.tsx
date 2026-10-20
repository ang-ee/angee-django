import { useDecision } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import type { ReactNode } from "react";
import { ErrorBanner, MetaSection, PaneRow, PaneRows, useRouteHref, type ContainerChild } from "@angee/ui";

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
  return <MetaSection headingLevel={2} title={t("catalogue.waitingRuns")}><PaneRows>{steps.map((step) =>
    <PaneRow key={step.id} icon="workflow" href={href("workflows.runs.record", { id: step.run.id })} title={step.run.display_name}
      detail={step.is_mapped ? t("timeline.mappedStep", { label: step.node_label, index: step.map_index + 1 }) : step.node_label} />)}</PaneRows></MetaSection>;
}

/** The run a decision waits in, as a `decisions#origin` child. */
export const decisionRunOrigin: ContainerChild<ReactNode> = { content: <DecisionRunOrigin /> };
