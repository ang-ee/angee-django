import { useAuthoredQuery } from "@angee/refine";
import {
  AbsoluteTime, ErrorBanner, PaneRow, PaneRows, RecordReferenceRow, Skeleton, SkeletonStatus, StatusBadge, useEnumOptions, useRouteHref,
  useResourceRecordHrefLookup, type ChatterViewContext, type ContainerChild, type RelatedGroupContent, type RelatedGroupLabel,
} from "@angee/ui";
import { useMemo, type ReactElement } from "react";

import { DecisionStepsDocument, RecordRelatedRunsDocument, RUN_MODEL, RUN_MODELS, RunRelatedDocument, STEP_RUN_MODEL } from "./documents.console";
import { enWorkflowsMessages, useWorkflowsT } from "./i18n";

const DECISION_MODEL = "decisions.Decision";
const RELATED_MODELS = [...RUN_MODELS, DECISION_MODEL] as const;
/** The roles a record plays in a run, most telling first: the one it keeps when several steps touched it. */
const ROLES = ["source", "created", "changed", "read"] as const;
type Role = (typeof ROLES)[number];
const ROLE_LABELS = {
  source: "related.role.source", created: "related.role.created", changed: "related.role.changed", read: "related.role.read",
} as const;

function recordId(context: ChatterViewContext): string {
  return context.view.kind === "record" ? context.view.sqid ?? "" : "";
}

function useRunRelated(runId: string) {
  const variables = useMemo(() => ({ run: runId }), [runId]);
  return useAuthoredQuery(RunRelatedDocument, variables, {
    enabled: Boolean(runId), models: RELATED_MODELS, records: [{ model: RUN_MODEL, id: runId }],
  });
}

export interface RunRecord {
  model: string;
  id: string;
  role: Role;
  steps: readonly string[];
}

/** The distinct records a run worked on: the subject first, then what it created, changed and read. */
export function runRecords(data: ReturnType<typeof useRunRelated>["data"]): readonly RunRecord[] {
  if (!data) return [];
  const run = data.workflowrun_by_pk;
  const byKey = new Map<string, { model: string; id: string; role: Role; steps: Set<string> }>();
  const keep = (model: string | null | undefined, id: string | null | undefined, role: Role, step?: string | null) => {
    if (!model || !id) return;
    const key = `${model}:${id}`;
    const entry = byKey.get(key) ?? { model, id, role, steps: new Set<string>() };
    if (ROLES.indexOf(role) < ROLES.indexOf(entry.role)) entry.role = role;
    if (step) entry.steps.add(step);
    byKey.set(key, entry);
  };
  keep(run?.subject_model, run?.subject_id, "source");
  keep(run?.trigger_event?.record_model, run?.trigger_event?.record_id, "source");
  for (const row of data.steprecord ?? []) {
    const role: Role = row.operation === "created" ? "created" : row.operation === "changed" ? "changed" : "read";
    keep(row.record_model, row.record_id, role, row.step_run?.node_label);
  }
  return [...byKey.values()].sort((a, b) => ROLES.indexOf(a.role) - ROLES.indexOf(b.role))
    .map((entry) => ({ model: entry.model, id: entry.id, role: entry.role, steps: [...entry.steps] }));
}

/** The records a run worked on, as a Related group of the run's page. */
export const runRecordsGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: enWorkflowsMessages["related.records"]!,
    useCount: (context) => {
      const query = useRunRelated(recordId(context));
      return query.data ? runRecords(query.data).length : undefined;
    },
    render: (context, label) => <RunRecordsGroup key={context.view.sqid} context={context} label={label} />,
  },
};

function RunRecordsGroup({ context, label }: { context: ChatterViewContext; label: RelatedGroupLabel }): ReactElement {
  const t = useWorkflowsT();
  const query = useRunRelated(recordId(context));
  const records = runRecords(query.data);
  const references = records.map(({ model, id }) => ({ model, id }));
  return <>{label()}<li>
    {query.error ? <ErrorBanner description={query.error.message} />
      : !query.data ? <SkeletonStatus label={t("related.loading")} className="space-y-2 p-2"><Skeleton className="h-8" /><Skeleton className="h-8" /></SkeletonStatus>
        : <PaneRows>{records.map((record) => <li key={`${record.model}:${record.id}`}>
          <RecordReferenceRow model={record.model} id={record.id} references={references}
            icon={record.role === "source" ? "file" : "link"}
            detail={t(ROLE_LABELS[record.role])} sub={record.steps.join(" · ") || undefined} />
        </li>)}</PaneRows>}
  </li></>;
}

/** The decisions a run asked, with their verdicts, as a Related group of the run's page. */
export const runDecisionsGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 20,
  content: {
    label: enWorkflowsMessages["related.decisions"]!,
    useCount: (context) => useRunRelated(recordId(context)).data?.steprun?.filter((step) => step.decision).length,
    render: (context, label) => <RunDecisionsGroup key={context.view.sqid} context={context} label={label} />,
  },
};

function RunDecisionsGroup({ context, label }: { context: ChatterViewContext; label: RelatedGroupLabel }): ReactElement {
  const t = useWorkflowsT();
  const routeHref = useRouteHref();
  const query = useRunRelated(recordId(context));
  const steps = query.data?.steprun?.flatMap((step) => step.decision ? [{ ...step, decision: step.decision }] : []) ?? [];
  return <>{label()}<li>
    {query.error ? <ErrorBanner description={query.error.message} />
      : !query.data ? <SkeletonStatus label={t("related.loading")} className="space-y-2 p-2"><Skeleton className="h-8" /></SkeletonStatus>
        : <PaneRows>{steps.map((step) => <li key={step.id}>
          <PaneRow icon="check" tone={step.decision.is_open ? "warning" : "success"} needsMe={step.decision.is_open}
            title={step.decision.kind_label}
            detail={step.decision.is_open ? t("related.open") : step.decision.verdict_label}
            meta={<AbsoluteTime value={step.decision.answered_at ?? step.decision.created_at} display="date" />}
            sub={[step.is_mapped ? t("timeline.mappedStep", { label: step.node_label, index: step.map_index + 1 }) : step.node_label,
              step.decision.answered_by?.display_name].filter(Boolean).join(" · ")}
            href={routeHref.maybe("decisions.inbox.record", { id: step.decision.id })} />
        </li>)}</PaneRows>}
  </li></>;
}

function useRecordRuns(context: ChatterViewContext) {
  const model = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const id = recordId(context);
  const records = useMemo(() => [{ model, id }], [model, id]);
  return useAuthoredQuery(RecordRelatedRunsDocument, { records }, {
    enabled: Boolean(model && id), models: [...RUN_MODELS], records, relatedModels: [...RUN_MODELS],
  });
}

/** Runs are their own record pages and steps are theirs; every other record lists the runs that worked on it. */
function hasRuns(context: ChatterViewContext): boolean {
  const model = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  return Boolean(model) && model !== RUN_MODEL && model !== STEP_RUN_MODEL && model !== DECISION_MODEL;
}

/** The runs that worked on a record, as a Related group of every record page. */
export const recordRunsGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 60,
  content: {
    label: enWorkflowsMessages["related.runs"]!,
    when: hasRuns,
    useCount: (context) => {
      const query = useRecordRuns(context);
      return query.data ? query.data.record_timeline.records.flatMap((entry) => entry.runs).length : undefined;
    },
    render: (context, label) => <RecordRunsGroup key={context.view.sqid} context={context} label={label} />,
  },
};

function RecordRunsGroup({ context, label }: { context: ChatterViewContext; label: RelatedGroupLabel }): ReactElement {
  const t = useWorkflowsT();
  const recordHref = useResourceRecordHrefLookup();
  const statuses = useEnumOptions(RUN_MODEL, "status", { casing: "upper" });
  const query = useRecordRuns(context);
  // Newest first: the run that last worked on the record is the one a reader looks for.
  const runs = [...(query.data?.record_timeline.records.flatMap((entry) => entry.runs) ?? [])].reverse();
  return <>{label()}<li>
    {query.error ? <ErrorBanner description={query.error.message} />
      : !query.data ? <SkeletonStatus label={t("related.loading")} className="space-y-2 p-2"><Skeleton className="h-8" /></SkeletonStatus>
        : <PaneRows>{runs.map((run) => <li key={run.id}>
          <PaneRow icon="workflow" title={run.version?.workflow?.display_name ?? run.display_name}
            detail={run.outcome_label || undefined}
            meta={<StatusBadge value={run.status} options={statuses} display="dot" />}
            sub={<AbsoluteTime value={run.created_at} display="datetime" />}
            href={recordHref(RUN_MODEL, run.id)} />
        </li>)}</PaneRows>}
  </li></>;
}

function useDecisionSteps(decisionId: string) {
  const variables = useMemo(() => ({ decision: decisionId }), [decisionId]);
  return useAuthoredQuery(DecisionStepsDocument, variables, {
    enabled: Boolean(decisionId), models: [DECISION_MODEL, STEP_RUN_MODEL],
    records: [{ model: DECISION_MODEL, id: decisionId }], relatedModels: [STEP_RUN_MODEL],
  });
}

/** The run and step that asked a decision, as a Related group of the decision's page. */
export const decisionRunGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 20,
  content: {
    label: enWorkflowsMessages["related.run"]!,
    useCount: (context) => useDecisionSteps(recordId(context)).data?.steprun.filter((step) => step.run).length,
    render: (context, label) => <DecisionRunGroup key={context.view.sqid} context={context} label={label} />,
  },
};

function DecisionRunGroup({ context, label }: { context: ChatterViewContext; label: RelatedGroupLabel }): ReactElement {
  const t = useWorkflowsT();
  const recordHref = useResourceRecordHrefLookup();
  const statuses = useEnumOptions(STEP_RUN_MODEL, "status", { casing: "upper" });
  const query = useDecisionSteps(recordId(context));
  const steps = query.data?.steprun.flatMap((step) => step.run ? [{ ...step, run: step.run }] : []) ?? [];
  return <>{label()}<li>
    {query.error ? <ErrorBanner description={query.error.message} />
      : !query.data ? <SkeletonStatus label={t("related.loading")} className="space-y-2 p-2"><Skeleton className="h-8" /></SkeletonStatus>
        : <PaneRows>{steps.map((step) => <li key={step.id}>
          <PaneRow icon="workflow" title={step.run.version?.workflow?.display_name ?? step.run.display_name}
            detail={step.is_mapped ? t("timeline.mappedStep", { label: step.node_label, index: step.map_index + 1 }) : step.node_label}
            meta={<StatusBadge value={step.status} options={statuses} display="dot" />}
            href={recordHref(RUN_MODEL, step.run.id)} />
        </li>)}</PaneRows>}
  </li></>;
}
