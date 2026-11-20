import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action, Field, Form, Group, optionToken, registerForm, useAppRuntime, useEnumOptions, useImplConfigFields,
  useRecordActionMutation, useT, type RecordTabDescriptor, type RegisteredFormProps,
} from "@angee/ui";

import { useWebformsT } from "../i18n";
import { FORM_MODEL } from "../resources";
import { FormAliases } from "./FormAliases";
import { UndeliveredNotice } from "./UndeliveredNotice";

/** What the record reads beside its fields, to say how many responses still wait for delivery. */
const UNDELIVERED_FIELDS = ["undelivered_count", "undelivered_expires_at"];

/** What choosing a target replaces whole, whatever was typed into it. */
const TARGET_CONFIG = ["target_config"];

/**
 * A target's configuration is not another's: choosing another target starts its
 * configuration empty. That is also what a target that takes none must be sent,
 * since the server refuses configuration on it. Choosing the target the form
 * was loaded with changes nothing: its configuration comes back with it, and
 * the form is again what is stored.
 */
function targetConfigFor(target: unknown, _values: unknown, loaded?: Record<string, unknown>): { target_config: unknown } {
  const stored = loaded !== undefined && optionToken(loaded.target_class) === optionToken(target);
  return { target_config: stored ? loaded.target_config ?? {} : {} };
}

/**
 * A web form as staff manage it, in the order a person decides it: its name,
 * what a response becomes and how that target is configured, the theme its public
 * page wears, how long a response is kept and, on a saved form, its public
 * addresses. Whether the form takes submissions is shown, never edited: it changes
 * through the form's own verbs, and the version it serves through publishing one.
 * While responses wait for delivery, the record's heading says so and links to them.
 */
function WebformForm({ resource: _resource, recordTabs, returning, headerExtras, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useWebformsT();
  const selected = React.useMemo(() => [...new Set([...(returning ?? []), ...UNDELIVERED_FIELDS])], [returning]);
  // The form's own tab first, then whatever its caller adds: the record's owner refuses a repeated id.
  const tabs = React.useMemo<readonly RecordTabDescriptor[]>(() => [
    { id: "addresses", label: t("form.tabs.addresses"), render: (context) => <FormAliases {...context} /> },
    ...(recordTabs ?? []),
  ], [recordTabs, t]);
  const targetOptions = useEnumOptions(FORM_MODEL, "target_class");
  // A target that declares its configuration is edited field by field. One that declares none shows
  // nothing. The raw value is kept only for a target whose declaration this form has no fields for.
  const targetConfig = useImplConfigFields(FORM_MODEL, "target_class", undefined, "target_config");
  // The themes installed in this deployment, by their own names; the empty choice follows its default.
  const { themes } = useAppRuntime();
  const themeT = useT("themes");
  const themeOptions = React.useMemo(() => [
    { value: "", label: t("form.themeDefault") },
    ...themes.map(({ definition }) => ({ value: definition.id, label: themeT(definition.labelKey) })),
  ], [t, themeT, themes]);
  const [open] = useRecordActionMutation<ActionFieldName>("open_webform", {
    idArgument: "form", invalidateModels: [FORM_MODEL],
  });
  const [close] = useRecordActionMutation<ActionFieldName>("close_webform", {
    idArgument: "form", invalidateModels: [FORM_MODEL],
  });
  return (
    <Form {...props} resource={FORM_MODEL} recordTabs={tabs} returning={selected}
      headerExtras={(context) => <>{headerExtras?.(context)}<UndeliveredNotice {...context} /></>}>
      <Field name="name" title />
      <Field name="status" widget="statusbar" status readOnly />
      <Field name="target_class" label={t("form.target")} widget="select" options={targetOptions}
        prefill={targetConfigFor} prefillReplace={TARGET_CONFIG} />
      <Field name="target_config" label={t("form.targetConfig")} widget="json"
        showWhen={(values) => targetConfig.hasSchema(values.target_class)
          && !targetConfig.fields.some((field) => field.showWhen?.(values))} />
      {targetConfig.fields.map((field) => <Field key={field.name} {...field} />)}
      <Group columns={2}>
        <Field name="theme_id" label={t("form.theme")} description={t("form.themeHint")} widget="select" options={themeOptions} />
        <Field name="retention_days" label={t("form.retention")} description={t("form.retentionHint")} />
      </Group>
      <Action id="open" label={t("form.open")} primary
        visibleWhen={(row) => optionToken(row.status) !== "published"} run={open} />
      <Action id="close" label={t("form.close")}
        visibleWhen={(row) => optionToken(row.status) === "published"}
        confirm={{ title: t("form.close"), body: t("form.closeConfirm") }} run={close} />
    </Form>
  );
}

export const webformForm = registerForm(FORM_MODEL, WebformForm);
