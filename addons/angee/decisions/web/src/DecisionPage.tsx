import { useMemo, useRef, type ReactElement } from "react";
import { useWatch } from "react-hook-form";
import { useAuthoredQuery } from "@angee/refine";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  actionOutcomeSubmitResult, ActionFormProvider, Button, DescriptorFieldList, EmptyState, ErrorBanner,
  formatDateTime, LoadingPanel, MetaGrid, MetaSection,
  Page, PageBody, parseFormSpecPayload, RecordChrome, RecordHeader, RecordReference, TextLink,
  useActionForm, useActionOutcomeMutation, useAppRuntime, useEnumValueLabel, useRouteHref, useRouteParam, useUiT,
} from "@angee/ui";

import { DecisionContext } from "./DecisionContext";
import { decisionForm, type DecisionValues } from "./decision-form";
import { DECISION_MODEL, DECISION_MODELS, DecisionDocument, DecisionSeatsDocument, type Decision } from "./documents.console";
import { useDecisionsT } from "./i18n";
import { DecisionContentOutlet, DecisionContentProvider, DecisionOriginOutlet } from "./slots";

export function DecisionPage(): ReactElement {
  const id = useRouteParam("id");
  const t = useDecisionsT();
  const query = useAuthoredQuery(DecisionDocument, { id: id ?? "" }, { enabled: Boolean(id), models: DECISION_MODELS });
  const decision = query.data?.decisions_by_pk;
  if (query.isLoading) return <LoadingPanel />;
  if (!decision) return query.error
    ? <ErrorBanner description={t("decision.unavailable")} actions={<Button onClick={() => void query.refetch()}>{t("decision.reload")}</Button>} />
    : <EmptyState title={t("decision.unavailable")} />;
  return <DecisionReview key={`${decision.id}:${decision.is_open}`} decision={decision}
    reload={async () => (await query.refetch({ throwOnError: true })).data?.decisions_by_pk ?? null} />;
}

/** A frozen question keeps its draft until explicit reload or settlement. */
export function DecisionReview({ decision, reload }: {
  decision: Decision; reload: () => Promise<Decision | null>;
}): ReactElement {
  const t = useDecisionsT();
  const uiT = useUiT();
  const { widgets } = useAppRuntime();
  const definition = useMemo(() => {
    try { return decisionForm(decision.form_schema, widgets, t, uiT); }
    catch { return null; }
  }, [decision.form_schema, widgets, t, uiT]);
  if (!definition) return <ErrorBanner description={t("decision.invalidForm")} />;
  return <DecisionReviewForm decision={decision} reload={reload} definition={definition} />;
}

function DecisionReviewForm({ decision, reload, definition }: {
  decision: Decision; reload: () => Promise<Decision | null>; definition: ReturnType<typeof decisionForm>;
}): ReactElement {
  const t = useDecisionsT();
  const label = useEnumValueLabel(DECISION_MODEL);
  const revision = useRef(decision.revision);
  const [decide] = useActionOutcomeMutation<ActionFieldName>("decide", { dataProviderName: "console", invalidateModels: DECISION_MODELS });
  const actionForm = useActionForm<DecisionValues>({
    defaultValues: definition.initial(parseFormSpecPayload(decision.resolution)),
    resolver: definition.resolver,
    fieldNames: definition.fieldNames,
    genericErrorMessage: t("decision.failed"),
    submit: async ({ action, ...values }) => {
      const outcome = await decide(decision.id, { revision: revision.current, action, values });
      if (outcome?.validationErrors?.revision) return { status: "conflict", message: t("decision.conflict") };
      // Invalid answers consume an attempt and revision on the server too.
      if (!outcome?.ok) {
        try {
          const fresh = await reload();
          if (fresh) revision.current = fresh.revision;
        } catch { /* Preserve the server's validation result when refresh is unavailable. */ }
      }
      const result = actionOutcomeSubmitResult(outcome);
      return result.status === "ok" ? { ...result, message: t("decision.recorded") } : result;
    },
  });
  const action = useWatch({ control: actionForm.form.control, name: "action" });
  const fields = useMemo(() => definition.fields(action), [definition, action]);
  const readOnly = !decision.is_open || !decision.can_act || actionForm.saveConflict;
  const reloadForm = async () => {
    try {
      const fresh = await reload();
      if (fresh) { revision.current = fresh.revision; actionForm.form.reset(definition.initial(parseFormSpecPayload(fresh.resolution))); }
    } catch { actionForm.form.setError("root.server", { type: "conflict", message: t("decision.conflict") }); }
  };
  return <ActionFormProvider {...actionForm.form}>
    <DecisionContentProvider value={{ decision, basis: decision.basis, context: decision.context }}>
      <Page>
        <RecordHeader title={label("kind", decision.kind)} status={{ label: label("verdict", decision.verdict) }}
          actions={<RecordChrome value={{ resource: DECISION_MODEL, canonicalResource: DECISION_MODEL,
            dataProviderName: "console", recordId: decision.id, record: decision, formReadOnly: readOnly }} />} />
        <PageBody className="space-y-6">
          <MetaGrid rows={[
            [t("decision.requester"), decision.requester?.display_name],
            [t("decision.subject"), <RecordReference model={decision.record_model_label} id={decision.record_public_id} />],
            [t("decision.expiry"), formatDateTime(decision.expires_at) || null],
          ]} />
          <DecisionOriginOutlet />
          <DecisionContext context={decision.context} />
          <form aria-label={t("decision.title")} className="space-y-4" noValidate
            onSubmit={(event) => { event.preventDefault(); if (!readOnly) void actionForm.run(); }}>
            <fieldset disabled={readOnly || actionForm.submitting} className="space-y-4">
              <DecisionContentOutlet />
              <DescriptorFieldList fields={definition.actionFields} readOnly={readOnly} />
              <DescriptorFieldList key={String(action)} fields={fields} readOnly={readOnly} />
            </fieldset>
            {actionForm.formError ? <ErrorBanner description={actionForm.formError} actions={actionForm.saveConflict
              ? <Button type="button" onClick={() => void reloadForm()}>{t("decision.reload")}</Button> : undefined} /> : null}
            {decision.is_open ? <Button type="submit" disabled={readOnly || actionForm.submitting}>{t("decision.submit")}</Button>
              : <MetaGrid rows={[
                [t("decision.verdict"), label("verdict", decision.verdict)],
                [t("decision.closedReason"), label("closed_reason", decision.closed_reason)],
                [t("decision.resolver"), decision.resolved_by?.display_name],
                [t("decision.resolvedAt"), formatDateTime(decision.resolved_at) || null],
              ]} />}
          </form>
          <DecisionSeats decision={decision} />
        </PageBody>
      </Page>
    </DecisionContentProvider>
  </ActionFormProvider>;
}

function DecisionSeats({ decision }: { decision: Decision }): ReactElement {
  const t = useDecisionsT();
  const href = useRouteHref();
  const label = useEnumValueLabel(DECISION_MODEL);
  const query = useAuthoredQuery(DecisionSeatsDocument, { group: decision.group.id }, { models: DECISION_MODELS });
  const seats = query.data?.decisions.filter((seat) => seat.id !== decision.id) ?? [];
  return <MetaSection title={t("decision.seats")}>
    <p>{t("decision.seatsDescription")}</p>
    {query.isLoading ? <LoadingPanel /> : query.error ? <ErrorBanner description={t("decision.unavailable")} />
      : seats.length ? <ul className="space-y-2">{seats.map((seat) => <li key={seat.id}>
        <TextLink href={href("decisions.inbox.record", { id: seat.id })}>{t("decision.seat", { number: seat.index + 1 })}</TextLink>
        {" · "}{seat.assignees.map((person) => person.display_name).join(", ")}{" · "}{label("verdict", seat.verdict)}
        {seat.closed_reason ? <> · {label("closed_reason", seat.closed_reason)}</> : null}
      </li>)}</ul> : <p>{t("decision.noSeats")}</p>}
  </MetaSection>;
}
