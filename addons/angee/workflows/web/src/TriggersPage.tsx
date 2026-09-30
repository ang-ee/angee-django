import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action, Column, DrawerResourceList, Field, Form, Group, List, ResourceList,
  useEnumOptions, useRecordActionMutation, useRouteHref, type StringIdRow,
} from "@angee/ui";
import { useWorkflowsT } from "./i18n";
import { TRIGGER_MODEL } from "./triggers";
import { TriggerEventsList } from "./TriggerEventsPage";
import { TriggerGrantsTab } from "./TriggerGrantsTab";

export function TriggersPage() { return <TriggersList />; }

/** One declaration owns routed triggers and the workflow's filtered record tab. */
export function TriggersList({ workflowId }: { workflowId?: string }) {
  const t = useWorkflowsT();
  const href = useRouteHref();
  const sourceOptions = useEnumOptions(TRIGGER_MODEL, "source");
  const View = workflowId ? DrawerResourceList<StringIdRow> : ResourceList<StringIdRow>;
  const [enable] = useRecordActionMutation<ActionFieldName>("enable_workflow_trigger", {
    dataProviderName: "console", invalidateModels: [TRIGGER_MODEL],
  });
  const [disable] = useRecordActionMutation<ActionFieldName>("disable_workflow_trigger", {
    dataProviderName: "console", invalidateModels: [TRIGGER_MODEL],
  });
  return <View resource={TRIGGER_MODEL} {...(workflowId ? {} : { placement: "inline" as const, routed: true })}
    presentation={workflowId ? "embedded" : undefined} baseFilter={workflowId ? { workflow: { exact: workflowId } } : undefined}
    rowHref={workflowId ? (row) => href("workflows.triggers.record", { id: row.id }) : undefined}
    recordTabs={[
      { id: "grants", label: t("trigger.grants"), render: ({ recordId }) => <TriggerGrantsTab recordId={recordId} /> },
      { id: "events", label: t("trigger.events"), render: ({ recordId }) => <TriggerEventsList triggerId={recordId} /> },
    ]}>
    <List emptyContent={t("trigger.empty")}>
      <Column field="display_name" header={t("trigger.title")} />
      <Column field="source" header={t("trigger.source")} />
      <Column field="enabled" header={t("trigger.enabled")} widget="boolean" />
      <Column field="disabled_reason" header={t("trigger.disabledReason")} />
    </List>
    <Form returning={["enabled", "source", "source_model", "can_edit", "enable_preview.grants", "enable_preview.run_readers"]} readOnlyWhen={(row) => row.can_edit !== true}
      deleteVisibleWhen={(row) => row.can_edit === true}>
      <Field name="display_name" title readOnly />
      <Group columns={2}>
        <Field name="workflow" label={t("run.workflow")} createOnly readOnly={Boolean(workflowId)} defaultValue={workflowId} />
        <Field name="source" label={t("trigger.source")} createOnly widget="select" options={sourceOptions} defaultValue="record_changed" />
        <Field name="model_label" label={t("catalogue.subjectModel")} createOnly defaultValue="" description={t("trigger.modelHint")} />
        <Field name="source_model" label={t("trigger.resolvedModel")} readOnly showWhen={(row) => Boolean(row.source_model) && !row.model_label} />
        <Field name="enabled" label={t("trigger.enabled")} readOnly widget="boolean" />
      </Group>
      <Field name="condition" label={t("trigger.condition")} widget="angee.workflows.condition" defaultValue={{}} />
      <Field name="disabled_reason" label={t("trigger.disabledReason")} readOnly widget="textarea" showWhen={(row) => Boolean(row.disabled_reason)} />
      <Action id="enable" label={t("trigger.enable")} primary
        visibleWhen={(row) => row.can_edit === true && row.enabled === false && row.enable_preview != null}
        confirm={(row) => {
          const preview = row.enable_preview as { grants: string[]; run_readers: string[] };
          return {
            title: t("trigger.enableConfirm"),
            body: <div>
              <p>{t("trigger.enableGrants")}</p>
              <ul>{preview.grants.map((grant) => <li key={grant}>{grant}</li>)}</ul>
              <p>{t("trigger.enableReaders")}</p>
              {preview.run_readers.length
                ? <ul>{preview.run_readers.map((reader) => <li key={reader}>{reader}</li>)}</ul>
                : <p>{t("trigger.noRunReaders")}</p>}
            </div>,
          };
        }} run={enable} />
      <Action id="disable" label={t("trigger.disable")} visibleWhen={(row) => row.can_edit === true && row.enabled === true} run={disable} />
    </Form>
  </View>;
}
