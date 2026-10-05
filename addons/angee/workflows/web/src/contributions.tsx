import { useDecision } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import { useMemo, type ReactNode } from "react";
import { ErrorBanner, MetaSection, REFINE_CREATE_ID, TextLink, useRouteHref, type ChatterTabContent, type ContainerChild } from "@angee/ui";

import { DecisionWaitingRunsDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";

import { RecordTimeline } from "./RecordTimeline";
import { useRecordTimelineAttention } from "./timeline-pane";

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
    <li key={step.id}><TextLink href={href("workflows.runs.record", { id: step.run.id })}>
      {step.run.display_name}</TextLink>{" · "}{step.node_label}{step.is_mapped ? ` [${step.map_index}]` : ""}</li>)}</ul></MetaSection>;
}

/** The record aside uses the same timeline a page can publish on the left. */
export const recordTimelineTab: ContainerChild<ChatterTabContent> = {
  sequence: 40,
  content: {
    icon: "versions", label: <TimelineLabel />,
    useCount: ({ view, route }) => {
      const record = useMemo(() => ({ model: route!.canonicalLabel!, id: view.sqid! }), [route?.canonicalLabel, view.sqid]);
      return useRecordTimelineAttention(record);
    },
    when: ({ view, route }) => view.kind === "record" && Boolean(view.sqid && route?.canonicalLabel) && view.sqid !== REFINE_CREATE_ID
      && !route?.canonicalLabel?.startsWith("workflows."),
    render: ({ view, route }) => view.sqid && route?.canonicalLabel
      ? <RecordTimeline record={{ model: route.canonicalLabel, id: view.sqid }} /> : null,
  },
};

/** The run a decision waits in, as a `decisions#origin` child. */
export const decisionRunOrigin: ContainerChild<ReactNode> = { content: <DecisionRunOrigin /> };

function TimelineLabel() { const t = useWorkflowsT(); return <>{t("timeline.title")}</>; }
