import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action, Field, Form, Group, optionToken, registerForm, useEnumOptions, useRecordActionMutation,
  type RecordTabDescriptor, type RegisteredFormProps,
} from "@angee/ui";

import { useWebformsT } from "../i18n";
import { FORM_MODEL } from "../resources";
import { FormAliases } from "./FormAliases";
import { UndeliveredNotice } from "./UndeliveredNotice";

/** What the record reads beside its fields, to say how many responses still wait for delivery. */
const UNDELIVERED_FIELDS = ["undelivered_count", "undelivered_expires_at"];

/**
 * A web form as staff manage it: its name, what a response becomes, the theme its
 * public page wears, how long a response is kept and, on a saved form, its public
 * addresses. Whether the form takes submissions is shown, never edited: it changes
 * through the form's own verbs, and the version it serves through publishing one.
 * While responses wait for delivery, the record's heading says so and links to them.
 */
function WebformForm({ resource: _resource, recordTabs, returning, headerExtras, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useWebformsT();
  const selected = React.useMemo(() => [...new Set([...(returning ?? []), ...UNDELIVERED_FIELDS])], [returning]);
  const tabs = React.useMemo<readonly RecordTabDescriptor[]>(() => [
    { id: "addresses", label: t("form.tabs.addresses"), render: (context) => <FormAliases {...context} /> },
  ], [t]);
  const targetOptions = useEnumOptions(FORM_MODEL, "target_class");
  const [open] = useRecordActionMutation<ActionFieldName>("open_webform", {
    idArgument: "form", invalidateModels: [FORM_MODEL],
  });
  const [close] = useRecordActionMutation<ActionFieldName>("close_webform", {
    idArgument: "form", invalidateModels: [FORM_MODEL],
  });
  return (
    <Form {...props} resource={FORM_MODEL} recordTabs={recordTabs ?? tabs} returning={selected}
      headerExtras={(context) => <>{headerExtras?.(context)}<UndeliveredNotice {...context} /></>}>
      <Field name="status" widget="statusbar" status readOnly />
      <Field name="name" title />
      <Group columns={2}>
        <Field name="target_class" label={t("form.target")} widget="select" options={targetOptions} />
        <Field name="retention_days" label={t("form.retention")} description={t("form.retentionHint")} />
        <Field name="theme_id" label={t("form.theme")} description={t("form.themeHint")} />
      </Group>
      <Field name="target_config" label={t("form.targetConfig")} widget="json" />
      <Action id="open" label={t("form.open")} primary
        visibleWhen={(row) => optionToken(row.status) !== "published"} run={open} />
      <Action id="close" label={t("form.close")}
        visibleWhen={(row) => optionToken(row.status) === "published"}
        confirm={{ title: t("form.close"), body: t("form.closeConfirm") }} run={close} />
    </Form>
  );
}

export const webformForm = registerForm(FORM_MODEL, WebformForm);
