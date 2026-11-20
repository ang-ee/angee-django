import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action, Field, Form, Group, optionToken, registerForm, useEnumOptions, useRecordActionMutation,
  type RegisteredFormProps,
} from "@angee/ui";

import { useWebformsT } from "../i18n";
import { FORM_MODEL } from "../resources";

/**
 * A web form as staff manage it: its name, what a response becomes, the theme its
 * public page wears and how long a delivered response is kept. Whether the form
 * takes submissions is shown, never edited: it changes through the form's own
 * verbs, and the version it serves through publishing one.
 */
function WebformForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useWebformsT();
  const targetOptions = useEnumOptions(FORM_MODEL, "target_class");
  const [open] = useRecordActionMutation<ActionFieldName>("open_webform", {
    idArgument: "form", invalidateModels: [FORM_MODEL],
  });
  const [close] = useRecordActionMutation<ActionFieldName>("close_webform", {
    idArgument: "form", invalidateModels: [FORM_MODEL],
  });
  return (
    <Form {...props} resource={FORM_MODEL}>
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
