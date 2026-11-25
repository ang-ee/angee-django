import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { holdsPermission, type Row } from "@angee/metadata";
import {
  Action,
  Field,
  Form,
  Group,
  registerForm,
  useEnumOptions,
  useRecordActionMutation,
  type RecordTabDescriptor,
  type RegisteredFormProps,
} from "@angee/ui";

import { useMessagingTemplatesT } from "../i18n";
import { MESSAGE_TEMPLATE_MODEL } from "../resources";
import { MessageTemplateNotice } from "./MessageTemplateNotice";
import { MessageTemplatePreviewPane } from "./MessageTemplatePreviewPane";

/** The row facts the notice and the verbs read beside the form's fields. */
const ROW_FACTS = ["is_baseline", "is_overridden", "has_updated_default"];

/** Only a template fills a subject, a preheader and a layout; the server says which kinds do. */
function carriesMessageFields(values: Row): boolean {
  return values.carries_message_fields === true;
}

/**
 * A message template as an administrator reads and edits it: its name as the
 * title; what it is (kind, key and language), entered when it is created and
 * fixed after; for a template, its subject, preheader and layout; its format, its
 * source and plain-text source; the values it reads; and its description. The
 * insert takes only the name and the identity and the server seeds the rest, so
 * a create form shows nothing else. The values it is previewed with are a pane of
 * their own, beside the saved row's Preview.
 *
 * A shipped default is read-only (it projects no `write`); Override starts the
 * deployment's override of it and opens it. An override whose shipped default
 * changed offers Keep override. Delete shows where the row projects `delete`:
 * deleting an override resets it to its shipped default. The notice above the
 * sheet says which of these the row is.
 */
function MessageTemplateForm({
  resource: _resource, recordTabs, returning, headerExtras, ...props
}: RegisteredFormProps): React.ReactElement {
  const t = useMessagingTemplatesT();
  const selected = React.useMemo(() => [...new Set([...(returning ?? []), ...ROW_FACTS])], [returning]);
  // The form's own pane first, then whatever its caller adds: the record's owner refuses a repeated id.
  const tabs = React.useMemo<readonly RecordTabDescriptor[]>(() => [
    {
      id: "preview",
      label: t("tab.preview"),
      presentation: "full-bleed",
      visibleWhen: (record) => holdsPermission(record, "preview"),
      render: ({ recordId, active }) => <MessageTemplatePreviewPane template={recordId} active={active} />,
    },
    ...(recordTabs ?? []),
  ], [recordTabs, t]);
  const kindOptions = useEnumOptions(MESSAGE_TEMPLATE_MODEL, "kind");
  const formatOptions = useEnumOptions(MESSAGE_TEMPLATE_MODEL, "format");
  const [override] = useRecordActionMutation<ActionFieldName>("override_message_template", {
    idArgument: "template", linkTo: MESSAGE_TEMPLATE_MODEL, invalidateModels: [MESSAGE_TEMPLATE_MODEL],
  });
  const [keep] = useRecordActionMutation<ActionFieldName>("keep_message_template_override", {
    idArgument: "template", invalidateModels: [MESSAGE_TEMPLATE_MODEL],
  });
  return (
    <Form {...props} resource={MESSAGE_TEMPLATE_MODEL} returning={selected} recordTabs={tabs}
      deleteVisibleWhen={(record) => holdsPermission(record, "delete")}
      headerExtras={(context) => <>{headerExtras?.(context)}<MessageTemplateNotice record={context.record} /></>}>
      <Field name="name" title />
      <Group label={t("form.identity")} columns={2}>
        <Field name="kind" label={t("field.kind")} widget="select" options={kindOptions} createOnly />
        <Field name="key" label={t("field.key")} createOnly />
        <Field name="language" label={t("field.language")} createOnly />
        <Field name="carries_message_fields" readOnly hidden />
      </Group>
      <Group label={t("form.message")} columns={2}>
        <Field name="subject" label={t("field.subject")} showWhen={carriesMessageFields} />
        <Field name="preheader" label={t("field.preheader")} showWhen={carriesMessageFields} />
        <Field name="layout" label={t("field.layout")} showWhen={carriesMessageFields} />
      </Group>
      <Group label={t("form.content")} columns={1}>
        <Field name="format" label={t("field.format")} widget="select" options={formatOptions} />
        <Field name="source" label={t("field.source")} widget="textarea" body={false} />
        <Field name="text_source" label={t("field.textSource")} widget="textarea" body={false} />
      </Group>
      {/* A json field holds `{}` on a create form, which would show it there; the server seeds it. */}
      <Group label={t("form.variables")} columns={1} savedOnly>
        <Field name="variables" label={t("field.variables")} widget="json" />
      </Group>
      <Group label={t("form.details")} columns={1}>
        <Field name="description" label={t("field.description")} widget="textarea" body={false} />
      </Group>
      <Group label={t("form.sample")} pane="sample" savedOnly>
        <Field name="sample" label={t("field.sample")} widget="json" />
      </Group>
      <Action id="override" label={t("action.override")} permission="preview"
        visibleWhen={(record) => record.is_baseline === true && record.is_overridden !== true} run={override} />
      <Action id="keep-override" label={t("action.keepOverride")} permission="write"
        visibleWhen={(record) => record.has_updated_default === true} run={keep} />
    </Form>
  );
}

export const messageTemplateForm = registerForm(MESSAGE_TEMPLATE_MODEL, MessageTemplateForm);
