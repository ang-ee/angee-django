import { useDecision } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import type { ReactNode } from "react";
import { ErrorBanner, NavLink, useRouteHref, type ContainerChild } from "@angee/ui";

import { DecisionStepsDocument, STEP_RUN_MODEL } from "./documents.console";
import { useWorkflowsT } from "./i18n";

const DECISION_MODEL = "decisions.Decision";

/** Where a question came from: the run and step that asked it, waiting or settled. */
export function DecisionRunOrigin() {
  const { decision } = useDecision();
  const t = useWorkflowsT();
  const href = useRouteHref();
  const decisionId = decision.id;
  const query = useAuthoredQuery(DecisionStepsDocument, { decision: decisionId }, {
    models: [DECISION_MODEL, STEP_RUN_MODEL],
    records: [{ model: DECISION_MODEL, id: decisionId }],
    relatedModels: [STEP_RUN_MODEL],
  });
  if (query.isLoading) return null;
  if (query.error) return <ErrorBanner description={t("catalogue.originUnavailable")} />;
  const steps = query.data?.steprun.flatMap((step) => step.run ? [{ ...step, run: step.run }] : []) ?? [];
  if (!steps.length) return null;
  return <span>{t("origin.askedIn")} {steps.map((step, index) => <span key={step.id}>{index ? "; " : ""}
    <NavLink variant="inline" href={href("workflows.runs.record", { id: step.run.id })}>{step.run.version?.workflow?.display_name ?? step.run.display_name}</NavLink>
    {" · "}{step.is_mapped ? t("timeline.mappedStep", { label: step.node_label, index: step.map_index + 1 }) : step.node_label}
  </span>)}</span>;
}

/** The run a decision was asked in, as a `decisions#origin` child. */
export const decisionRunOrigin: ContainerChild<ReactNode> = { content: <DecisionRunOrigin /> };
