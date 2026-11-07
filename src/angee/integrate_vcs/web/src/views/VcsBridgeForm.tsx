import * as React from "react";
import { runActionResult, useAuthoredMutation } from "@angee/refine";
import { Action, Field, Form, registerForm, useEnumOptions, useImplConfigFields, useImplPrefill, useRecordAction, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { useIntegrateVcsT } from "../i18n";
import { IntegrateDiscoverRepositories } from "../documents";

const MODEL = "integrate_vcs.VcsBridge";

function VcsBridgeForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useIntegrateVcsT();
  const syncAction = useIntegrationSyncAction("sync_vcs_bridge", t("action.syncNow"));
  const [discover] = useAuthoredMutation(IntegrateDiscoverRepositories);
  const backendClassOptions = useEnumOptions(MODEL, "backend_class");
  const privateConfigReset = React.useMemo(() => ({ config: {} }), []);
  const backendClassPrefill = useImplPrefill(MODEL, "backend_class", privateConfigReset);
  const implConfig = useImplConfigFields(MODEL, "backend_class");

  const discoverRepositories = React.useCallback(
    async (id: string) => {
      const result = await discover({ vcsBridgeId: id, org: "" });
      return runActionResult(result?.discover_repositories);
    },
    [discover],
  );
  const discoverAll = useRecordAction(discoverRepositories);

  return (
      <Form {...props} resource={MODEL}>
        <Field name="display_name" title />
        <Field name="owner" />
        <Field name="vendor" />
        <Field
          name="backend_class"
          widget="select"
          options={backendClassOptions}
          prefill={backendClassPrefill}
          prefillPreserveDirty
          prefillReplace={["config"]}
          createOnly
        />
        <Field name="credential" />
        <Field name="lifecycle" widget="statusbar" status readOnly />
        <Field name="runtime_status" widget="statusBadge" readOnly />
        <Field
          name="config"
          widget="json"
          showWhen={(values) => !implConfig.hasSchema(values.backend_class)}
        />
        {implConfig.fields.map((field) => <Field key={field.name} {...field} />)}
        {IntegrationSyncFields({ label: t("bridge.group.sync") })}
        {/* Write-only signing secret — set on create, never read back. */}
        <Field name="webhook_secret" widget="text" kind="string" createOnly />
        {syncAction}
        <Action id="discover" label={t("vcs.discover")} run={discoverAll} />
      </Form>
  );
}

export const vcsBridgeForm = registerForm(MODEL, VcsBridgeForm);
