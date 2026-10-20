import { useAuthoredQuery } from "@angee/refine";
import {
  AbsoluteTime, ErrorBanner, PaneRow, PaneRows, RecordReferenceRow, Skeleton, SkeletonStatus, useRouteHref,
  type ChatterViewContext, type ContainerChild, type RelatedGroupContent, type RelatedGroupLabel,
} from "@angee/ui";
import { useMemo, type ReactElement } from "react";
import * as v from "valibot";

import { DecisionContextSchema } from "./DecisionContext";
import { DECISION_MODEL, DECISION_MODELS, DecisionDocument, RecordDecisionsDocument } from "./documents.console";
import { enDecisionsMessages, useDecisionsT } from "./i18n";

const RECORD_MODEL = "decisions.DecisionRecord";

function recordId(context: ChatterViewContext): string {
  return context.view.kind === "record" ? context.view.sqid ?? "" : "";
}

/** The same read as the decision's page, so the page and its aside share one cache entry. */
function useDecisionRead(id: string) {
  const variables = useMemo(() => ({ id }), [id]);
  return useAuthoredQuery(DecisionDocument, variables, { enabled: Boolean(id), models: DECISION_MODELS });
}

/** The records a decision concerns, as a Related group of the decision's page. */
export const decisionConcernsGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: enDecisionsMessages["related.concerns"]!,
    useCount: (context) => {
      const query = useDecisionRead(recordId(context));
      return query.data ? query.data.decisions_by_pk?.records.filter((record) => record.record_model && record.record_id).length ?? 0 : undefined;
    },
    render: (context, label) => <DecisionConcernsGroup key={context.view.sqid} context={context} label={label} />,
  },
};

function DecisionConcernsGroup({ context, label }: { context: ChatterViewContext; label: RelatedGroupLabel }): ReactElement {
  const t = useDecisionsT();
  const query = useDecisionRead(recordId(context));
  const decision = query.data?.decisions_by_pk;
  const parsed = v.safeParse(DecisionContextSchema, decision?.context);
  const references = parsed.success ? [...parsed.output.references, ...parsed.output.facts.flatMap((fact) => fact.evidence)] : [];
  const records = decision?.records.flatMap((record) => record.record_model && record.record_id
    ? [{ model: record.record_model, id: record.record_id, label: references.find((reference) => reference.id === record.record_id)?.label || undefined }]
    : []) ?? [];
  return <>{label()}<li>
    {query.error ? <ErrorBanner description={query.error.message} />
      : !query.data ? <SkeletonStatus label={t("related.loading")} className="space-y-2 p-2"><Skeleton className="h-8" /></SkeletonStatus>
        : <PaneRows>{records.map((record) => <li key={`${record.model}:${record.id}`}>
          <RecordReferenceRow model={record.model} id={record.id} references={records} icon="link" detail={record.label} />
        </li>)}</PaneRows>}
  </li></>;
}

function useRecordDecisions(context: ChatterViewContext) {
  const model = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  const id = recordId(context);
  const variables = useMemo(() => ({ model, id }), [model, id]);
  return useAuthoredQuery(RecordDecisionsDocument, variables, {
    enabled: Boolean(model && id), models: DECISION_MODELS, records: [{ model, id }], relatedModels: [DECISION_MODEL, RECORD_MODEL],
  });
}

function recordDecisions(data: ReturnType<typeof useRecordDecisions>["data"]) {
  const decisions = data?.decision_records.flatMap((link) => link.decision ? [link.decision] : []) ?? [];
  // Open questions first, then the newest answers.
  return decisions.sort((a, b) => Number(b.is_open) - Number(a.is_open)
    || (b.answered_at ?? b.created_at).localeCompare(a.answered_at ?? a.created_at));
}

/** Decisions are their own record pages; every other record lists the decisions that concern it. */
function concernsRecords(context: ChatterViewContext): boolean {
  const model = context.route?.canonicalLabel ?? context.route?.modelLabel ?? "";
  return Boolean(model) && model !== DECISION_MODEL && model !== RECORD_MODEL;
}

/** The decisions that concern a record, as a Related group of every record page. */
export const recordDecisionsGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 50,
  content: {
    label: enDecisionsMessages["related.decisions"]!,
    when: concernsRecords,
    useCount: (context) => {
      const query = useRecordDecisions(context);
      return query.data ? recordDecisions(query.data).length : undefined;
    },
    render: (context, label) => <RecordDecisionsGroup key={context.view.sqid} context={context} label={label} />,
  },
};

function RecordDecisionsGroup({ context, label }: { context: ChatterViewContext; label: RelatedGroupLabel }): ReactElement {
  const t = useDecisionsT();
  const routeHref = useRouteHref();
  const query = useRecordDecisions(context);
  const decisions = recordDecisions(query.data);
  return <>{label()}<li>
    {query.error ? <ErrorBanner description={query.error.message} />
      : !query.data ? <SkeletonStatus label={t("related.loading")} className="space-y-2 p-2"><Skeleton className="h-8" /></SkeletonStatus>
        : <PaneRows>{decisions.map((decision) => <li key={decision.id}>
          <PaneRow icon="check" tone={decision.is_open ? "warning" : "success"} needsMe={decision.is_open}
            title={decision.kind_label}
            detail={decision.is_open ? t("related.open") : decision.verdict_label}
            meta={<AbsoluteTime value={decision.answered_at ?? decision.created_at} display="date" />}
            sub={decision.answered_by?.display_name}
            href={routeHref.maybe("decisions.inbox.record", { id: decision.id })} />
        </li>)}</PaneRows>}
  </li></>;
}
