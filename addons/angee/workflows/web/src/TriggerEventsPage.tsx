import { Column, Field, Form, Group, List, RecordReference, ResourceList, relationValueId, useRouteHref, type StringIdRow } from "@angee/ui";
import { RUN_MODEL } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { TRIGGER_EVENT_MODEL } from "./triggers";

export function TriggerEventsPage() { return <TriggerEventsList />; }

/** The same retained-ledger view is used for direct run links and a trigger's record tab. */
export function TriggerEventsList({ triggerId }: { triggerId?: string }) {
  const t = useWorkflowsT();
  const href = useRouteHref();
  return <ResourceList<StringIdRow> resource={TRIGGER_EVENT_MODEL} hideCreate placement="inline" routed={!triggerId}
    presentation={triggerId ? "embedded" : undefined} fields={["record_model"]}
    baseFilter={triggerId ? { trigger: { exact: triggerId } } : undefined}
    rowHref={triggerId ? (row) => href("workflows.trigger-events.record", { id: row.id }) : undefined}>
    <List order={{ changed_at: "DESC" }} emptyContent={t("trigger.noEvents")}>
      <Column field="display_name" header={t("trigger.event")} />
      <Column field="record_id" header={t("trigger.record")} render={(row) => typeof row.record_model === "string" && typeof row.record_id === "string"
        ? <RecordReference model={row.record_model} id={row.record_id} /> : null} />
      <Column field="changed_at" header={t("trigger.changed")} />
      <Column field="admitted_at" header={t("trigger.admitted")} />
      <Column field="started_run" header={t("trigger.run")} render={(row) => {
        const id = relationValueId(row.started_run);
        return id ? <RecordReference model={RUN_MODEL} id={id} /> : null;
      }} />
      <Column field="rejection" header={t("trigger.rejection")} />
    </List>
    <Form readOnly returning={["record_model", "record_id"]} headerExtras={({ record }) =>
      typeof record?.record_model === "string" && typeof record.record_id === "string"
        ? <RecordReference model={record.record_model} id={record.record_id} /> : null}>
      <Field name="display_name" title />
      <Group columns={2}>
        <Field name="trigger" label={t("trigger.title")} />
        <Field name="started_run" label={t("trigger.run")} />
        <Field name="changed_at" label={t("trigger.changed")} />
        <Field name="evaluated_at" label={t("trigger.evaluated")} />
        <Field name="admitted_at" label={t("trigger.admitted")} />
      </Group>
      <Field name="rejection" label={t("trigger.rejection")} widget="textarea" />
    </Form>
  </ResourceList>;
}
