import { useAuthoredQuery } from "@angee/refine";
import { rowValueAtPath } from "@angee/metadata";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { useMemo, type ReactElement } from "react";
import {
  Action, Column, ErrorBanner, Field, Form, Group, LabeledDescriptorField, List, LoadingPanel,
  RecordReference, ResourceList, useActionOutcomeMutation, useAppRuntime,
  useEnumOptions, useRouteHref, useRuntimeAuth, useUiT,
  type RecordPanelContext, type ResourceViewFilter, type StringIdRow,
} from "@angee/ui";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";
import { parseFormSpecPayload } from "@angee/ui";

import { DecisionContext } from "./DecisionContext";
import { DECISION_MODEL, DECISION_MODELS, DecisionDocument } from "./documents.console";
import { useDecisionsT } from "./i18n";
import { DecisionContentOutlet, DecisionContentProvider, DecisionOriginOutlet, useDecisionContentEntries } from "./slots";

/** Open readable seats include delegated assignments; filters remain editable. */
export function InboxPage(): ReactElement {
  const t = useDecisionsT();
  const uiT = useUiT();
  const { user } = useRuntimeAuth();
  const { widgets } = useAppRuntime();
  const verdicts = useEnumOptions(DECISION_MODEL, "verdict");
  const closedReasons = useEnumOptions(DECISION_MODEL, "closed_reason");
  const [decide] = useActionOutcomeMutation<ActionFieldName>("decide", {
    dataProviderName: "console", invalidateModels: DECISION_MODELS,
  });
  const defaultFilter = useMemo<ResourceViewFilter>(() => ({ is_open: { exact: true } }), []);
  if (!user) return <LoadingPanel />;

  return (
    <ResourceList<StringIdRow>
      resource={DECISION_MODEL} placement="inline" routed hideCreate
      defaultFilter={defaultFilter}
      filterOptions={[
        { id: "assigned", label: t("inbox.assigned"), group: t("inbox.scope"), filter: { assignees: { exact: user.id } } },
        { id: "requested", label: t("inbox.requested"), group: t("inbox.scope"), filter: { requester: { exact: user.id } } },
        { id: "open", label: t("inbox.open"), group: t("inbox.state"), filter: { is_open: { exact: true } } },
        { id: "settled", label: t("inbox.settled"), group: t("inbox.state"), filter: { is_open: { exact: false } } },
      ]}
      recordTabs={[{ id: "context", label: t("context.title"), render: (context) => <DecisionDetails {...context} /> }]}
    >
      <List resource={DECISION_MODEL} fields={["subject_model"]} order={{ created_at: "DESC" }} emptyContent={t("inbox.empty")}>
        <Column field="kind_label" header={t("inbox.kind")} />
        <Column field="subject_id" header={t("inbox.subject")} render={(row) => {
          const id = row.subject_id;
          return typeof id === "string" && typeof row.subject_model === "string"
            ? <RecordReference model={row.subject_model} id={id} /> : null;
        }} />
        <Column field="requester.display_name" header={t("inbox.requester")} />
        <Column field="expires_at" header={t("inbox.expiresAt")} />
        <Column field="verdict" header={t("inbox.verdict")} widget="statusBadge" />
      </List>
      <Form resource={DECISION_MODEL} readOnly returning={["revision", "is_open", "permissions", "form_schema", "resolution", "subject_model", "subject_id"]}
        formExtras={({ record }) => {
          const assignees = Array.isArray(record?.assignees)
            ? record.assignees.map((value: unknown) => value && typeof value === "object" && "display_name" in value
              ? String(value.display_name) : "").filter(Boolean) : [];
          const statusOptions = record?.is_open === false && record.verdict === "PENDING" ? closedReasons : verdicts;
          const statusValue = statusOptions === closedReasons ? record?.closed_reason : record?.verdict;
          return <div className="space-y-4">
            <h3 className="border-b border-border-subtle pb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
              {t("decision.title")}
            </h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <div><div className="text-xs text-fg-muted">{t("inbox.kind")}</div><div>{String(record?.kind_label ?? "")}</div></div>
              <div><div className="text-xs text-fg-muted">{t("decision.status")}</div>
                <div>{statusOptions.find((option) => option.value === statusValue)?.label ?? String(statusValue ?? "")}</div></div>
              {assignees.length > 0 ? <div><div className="text-xs text-fg-muted">{t("decision.assignees")}</div>
                <div>{assignees.join(", ")}</div></div> : null}
            </div>
            {record?.is_open === false && record.verdict !== "PENDING" && record.resolution
              ? <DecisionAnswer schema={record.form_schema} resolution={record.resolution} /> : null}
          </div>;
        }}
        headerExtras={({ record }) => typeof record?.subject_model === "string" && typeof record.subject_id === "string"
          ? <RecordReference model={record.subject_model} id={record.subject_id} /> : null}>
        <Field name="is_open" hidden />
        <Field name="kind_label" title />
        <Field name="verdict" widget="statusbar" status options={verdicts} resolve={(row) =>
          row.is_open === false && row.verdict === "PENDING" && typeof row.closed_reason === "string"
            ? { name: "verdict", options: closedReasons.filter((option) => option.value.toUpperCase() === row.closed_reason),
                valueCodec: { toControl: () => row.closed_reason, fromControl: (value) => value } }
            : { name: "verdict", options: verdicts }} />
        <Field name="assignees" hidden />
        <Group label={t("decision.title")} columns={2}>
          <Field name="requester.display_name" label={t("decision.requester")}
            showWhen={(row) => Boolean(rowValueAtPath(row, "requester.display_name"))} />
          <Field name="expires_at" label={t("decision.expiry")} showWhen={(row) => row.is_open === false && Boolean(row.expires_at)} />
          <Field name="resolved_by.display_name" label={t("decision.resolver")} showWhen={(row) => row.is_open === false && Boolean(rowValueAtPath(row, "resolved_by.display_name"))} />
          <Field name="resolved_at" label={t("decision.resolvedAt")} showWhen={(row) => row.is_open === false && Boolean(row.resolved_at)} />
          <Field name="closed_reason" label={t("decision.closedReason")} showWhen={(row) => row.is_open === false && Boolean(row.closed_reason)} />
        </Group>
        <Action id="decide" label={t("decision.submit")} placement="toolbar" primary icon="check"
          permission="act" visibleWhen={(record) => record.is_open === true}
          args={({ record }) => {
            const definition = jsonSchemaActionArgs(record?.form_schema, widgets, { initialValues: record?.resolution, translate: uiT });
            return { ...definition, size: "lg" as const,
              fields: (values) => (typeof definition.fields === "function" ? definition.fields(values) : definition.fields)
                .map((field) => field.name === "action" ? { ...field, label: t("decision.action") } : field),
              content: typeof record?.id === "string" ? <DecisionDetails recordId={record.id} editing /> : undefined,
            };
          }}
          submit={async ({ action, ...values }, context) => {
            const record = context.record;
            if (typeof record?.id !== "string" || typeof record.revision !== "number") throw new Error(t("decision.unavailable"));
            const outcome = await decide(record.id, { revision: record.revision, action, values });
            if (!outcome?.ok) {
              try { await context.refresh?.(); } catch { /* Keep the server's answer errors if refresh is unavailable. */ }
              if (outcome?.validationErrors?.revision) return { status: "conflict", message: t("decision.conflict") };
            }
            return outcome;
          }}
        />
      </Form>
    </ResourceList>
  );
}

function DecisionAnswer({ schema, resolution }: { schema: unknown; resolution: unknown }): ReactElement {
  const t = useDecisionsT();
  const uiT = useUiT();
  const { widgets } = useAppRuntime();
  const values = useMemo(() => parseFormSpecPayload(resolution), [resolution]);
  const definition = useMemo(() => jsonSchemaActionArgs(schema, widgets, { initialValues: resolution, translate: uiT }),
    [schema, widgets, resolution, uiT]);
  const fields = typeof definition.fields === "function" ? definition.fields(values) : definition.fields;
  return <section className="space-y-3">
    <h3 className="border-b border-border-subtle pb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
      {t("decision.answer")}
    </h3>
    <div className="grid gap-4 sm:grid-cols-2">
      {fields.filter((field) => rowValueAtPath(values, field.name) !== undefined).map((field) =>
        <LabeledDescriptorField key={field.name} field={field.name === "action" ? { ...field, label: t("decision.action") } : field}
          value={rowValueAtPath(values, field.name)} dialogValues={values} readOnly onChange={() => {}} />)}
    </div>
  </section>;
}

/** Lazy retained context composes consumer slots in the record tab and action form. */
function DecisionDetails({ recordId, editing = false }: Pick<RecordPanelContext, "recordId"> & { editing?: boolean }): ReactElement {
  const t = useDecisionsT();
  const query = useAuthoredQuery(DecisionDocument, { id: recordId }, { models: DECISION_MODELS });
  const decision = query.data?.decisions_by_pk;
  const content = useDecisionContentEntries(decision?.kind ?? "");
  if (query.isLoading) return <LoadingPanel />;
  if (!decision) return <ErrorBanner description={t("decision.unavailable")} />;
  return <DecisionContentProvider value={{ decision, basis: decision.basis, context: decision.context }}>
    <div className="min-w-0 space-y-6 [overflow-wrap:anywhere]">
      <DecisionOriginOutlet />
      <DecisionContext context={decision.context} showFacts={content.length === 0} />
      <fieldset disabled={!editing}><DecisionContentOutlet /></fieldset>
      {!editing ? <DecisionSeats recordId={recordId} groupId={decision.group.id} /> : null}
    </div>
  </DecisionContentProvider>;
}

function DecisionSeats({ recordId, groupId }: { recordId: string; groupId: string }): ReactElement {
  const t = useDecisionsT();
  const href = useRouteHref();
  return <List resource={DECISION_MODEL} scope="local" presentation="embedded"
    baseFilter={{ group: { exact: groupId }, id: { ne: recordId } }} order={{ index: "ASC" }}
    rowHref={(row) => href("decisions.inbox.record", { id: String(row.id) })} emptyContent={t("decision.noSeats")}>
    <Column field="kind_label" header={t("decision.seats")} />
    <Column field="assignees" />
    <Column field="verdict" widget="statusBadge" />
    <Column field="closed_reason" />
  </List>;
}
