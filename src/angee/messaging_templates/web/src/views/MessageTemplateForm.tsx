import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { holdsPermission, type Row } from "@angee/metadata";
import {
  Action,
  Field,
  Form,
  Group,
  PaneToolbarHost,
  optionToken,
  registerForm,
  useEnumOptions,
  useRecordActionMutation,
  type RecordTabDescriptor,
  type RegisteredFormProps,
} from "@angee/ui";

import { useMessagingTemplatesT } from "../i18n";
import { MESSAGE_TEMPLATE_MODEL } from "../resources";
import { MessageTemplateNotice, offersKeepOverride, offersOverride } from "./MessageTemplateNotice";
import { MessageTemplatePreviewPane } from "./MessageTemplatePreviewPane";

/**
 * What the notice and the verbs read beside the form's fields: the row facts, and
 * the server's answer to whether each verb admits this reader on this row, which
 * the record's own read selects (`can_override` decides a permission per row).
 */
const ROW_FACTS = [
  "is_baseline", "is_overridden", "overrides_default", "has_updated_default", "can_override", "can_keep_override",
];

/** Only a template fills a subject, a preheader and a layout; the server says which kinds do. */
function carriesMessageFields(values: Row): boolean {
  return values.carries_message_fields === true;
}

/** Only an HTML row has a plain-text source; a Markdown row's plain text is rendered from its Markdown. */
function hasPlainTextSource(values: Row): boolean {
  return optionToken(values.format) === "html";
}

/**
 * What a format sets on the plain-text source: Markdown empties it, as the server requires of a
 * Markdown row, and the save then writes the empty text for the hidden field; HTML sets nothing, and
 * the form gives the field it shows again the value the row was loaded with, not an unsaved edit.
 */
function plainTextSourceFor(format: unknown): Record<string, unknown> {
  return optionToken(format) === "html" ? {} : { text_source: "" };
}

/**
 * A message template as an administrator reads and edits it: its name as the
 * title; what it is (kind, key and language), entered when it is created and
 * fixed after; for a template, its subject, preheader and layout; its format, its
 * source and, for HTML, its plain-text source; the values it reads; and its
 * description. The insert takes only the name and the identity and the server
 * seeds the rest, so a create form shows nothing else. The values it is previewed
 * with are a pane of their own, beside the saved row's Preview.
 *
 * A shipped default is read-only (it projects no `write`); Override starts the
 * deployment's override of it and opens it. An override whose shipped default
 * changed offers Keep override. Each verb shows where the server says it admits
 * the reader (`can_override`, `can_keep_override`) and the row is in the state
 * the verb changes. Delete shows where the row projects `delete`: deleting an
 * override resets it to its shipped default. The notice above the sheet says
 * which of these the row is.
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
      // The pane's toolbar row is the shared pane toolbar's, as every pane's is; the strip names the tab.
      render: ({ recordId, active, form }) => (
        <PaneToolbarHost>
          <MessageTemplatePreviewPane template={recordId} active={active}
            carriesMessageFields={carriesMessageFields(form.displayRecord ?? {})} />
        </PaneToolbarHost>
      ),
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
        <Field name="permissions" readOnly hidden />
      </Group>
      <Group label={t("form.message")} columns={2}>
        <Field name="subject" label={t("field.subject")} showWhen={carriesMessageFields} />
        <Field name="preheader" label={t("field.preheader")} showWhen={carriesMessageFields} />
        <Field name="layout" label={t("field.layout")} showWhen={carriesMessageFields} />
      </Group>
      <Group label={t("form.content")} columns={1}>
        {/* What switching the format does is said to whoever may switch it: a reader who may write the row. */}
        <Field name="format" label={t("field.format")} widget="select" options={formatOptions}
          prefill={plainTextSourceFor}
          resolve={(values) => (holdsPermission(values, "write") ? { description: t("field.formatHint") } : {})} />
        <Field name="source" label={t("field.source")} widget="textarea" body={false} />
        <Field name="text_source" label={t("field.textSource")} widget="textarea" body={false}
          showWhen={hasPlainTextSource} />
      </Group>
      <Group label={t("form.variables")} columns={1}>
        <Field name="variables" label={t("field.variables")} widget="json" />
      </Group>
      <Group label={t("form.details")} columns={1}>
        <Field name="description" label={t("field.description")} widget="textarea" body={false} />
      </Group>
      <Group label={t("form.sample")} pane="sample">
        <Field name="sample" label={t("field.sample")} widget="json" />
      </Group>
      <Action id="override" label={t("action.override")} visibleWhen={offersOverride} run={override} />
      <Action id="keep-override" label={t("action.keepOverride")} visibleWhen={offersKeepOverride} run={keep} />
    </Form>
  );
}

export const messageTemplateForm = registerForm(MESSAGE_TEMPLATE_MODEL, MessageTemplateForm);
