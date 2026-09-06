import * as React from "react";
import { runActionResult, useAuthoredMutation } from "@angee/refine";
import { Action, Column, Facet, Field, Form, Group, List, ResourceList, registerForm, useAuthoredResourceMutation, useEnumOptions, useImplConfigFields, useImplPrefill, useRecordAction, useRecordActionMutation, type FormSubmit, type RegisteredFormProps } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";
import type { DocumentVariables } from "@angee/refine";

import { useIntegrateVcsT } from "../i18n";
import {
  INTEGRATE_VCS_BRIDGE_INVALIDATES,
  IntegrateCreateVcsBridge,
  IntegrateDiscoverRepositories,
  IntegrateUpdateVcsBridge,
} from "../documents";

const MODEL = "integrate_vcs.VcsBridge";

/**
 * VCS bridges own repository discovery and source sync for one integration child row.
 */
export function VcsBridgesPage(): React.ReactElement {
  const t = useIntegrateVcsT();
  return (
    <ResourceList resource={MODEL} form={vcsBridgeForm} placement="inline" routed>
      <List resource={MODEL}>
        <Facet field="vendor" label={t("col.vendor")} labelField="display_name" />
        <Column field="display_name" />
        <Column field="backend_class" header={t("vcs.backendClass")} />
        <Column field="lifecycle" header={t("col.lifecycle")} widget="statusBadge" />
        <Column field="runtime_status" header={t("col.runtimeStatus")} widget="colorDot" />
        <Column field="sync_stage" />
        <Column field="last_sync_completed_at" />
      </List>
    </ResourceList>
  );
}

function VcsBridgeForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useIntegrateVcsT();
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_vcs_bridge");
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

  // A VCS bridge exposes no auto-CRUD insert/update root — its writes resolve the
  // backend impl key and re-materialise backend defaults — so both verbs save
  // through the bespoke mutations that own that.
  const [createBridge] = useAuthoredResourceMutation(IntegrateCreateVcsBridge, {
    invalidateModels: INTEGRATE_VCS_BRIDGE_INVALIDATES,
  });
  const [updateBridge] = useAuthoredResourceMutation(IntegrateUpdateVcsBridge, {
    invalidateModels: INTEGRATE_VCS_BRIDGE_INVALIDATES,
  });
  const submitBridge = React.useCallback<FormSubmit>(
    async (data, context) => {
      // `data` is FormView's normalized payload: relation fields are already flat
      // public ids. Only keys the form actually submitted are forwarded, so an
      // untouched patch field stays UNSET server-side instead of being cleared.
      const fields = pickPresent(data, [
        "display_name",
        "vendor",
        "owner",
        "credential",
        "account",
        "backend_class",
        "config",
      ]);
      // The write-only secret is named for its input key, not the read projection.
      if (data.webhookSecret !== undefined) fields.webhook_secret = data.webhookSecret;
      if (context.isCreate) {
        const variables = { data: fields } as DocumentVariables<typeof IntegrateCreateVcsBridge>;
        return (await createBridge(variables))?.create_vcs_bridge ?? null;
      }
      const variables = {
        data: { ...fields, id: context.id },
      } as DocumentVariables<typeof IntegrateUpdateVcsBridge>;
      return (await updateBridge(variables))?.update_vcs_bridge ?? null;
    },
    [createBridge, updateBridge],
  );

  return (
      <Form {...props} resource={MODEL} submit={submitBridge}>
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
        <Field name="lifecycle" widget="statusbar" readOnly />
        <Field name="runtime_status" readOnly />
        <Field
          name="config"
          widget="json"
          showWhen={(values) => !implConfig.hasSchema(values.backend_class)}
        />
        {implConfig.fields.map((field) => <Field key={field.name} {...field} />)}
        <Group label={t("bridge.group.sync")} columns={2}>
          <Field name="is_syncing" readOnly />
          <Field name="sync_stage" readOnly />
          <Field name="sync_error" readOnly />
          <Field name="sync_progress" widget="json" readOnly />
          <Field name="last_sync_summary" widget="json" readOnly />
          <Field name="last_sync_items" readOnly />
          <Field name="last_sync_completed_at" readOnly />
        </Group>
        <Field name="last_sync_status" readOnly />
        {/* Write-only signing secret — set on create, never read back. */}
        <Field name="webhookSecret" widget="text" kind="string" createOnly />
        <Action id="sync" label={t("action.syncNow")} icon="refresh" run={sync} />
        <Action id="discover" label={t("vcs.discover")} run={discoverAll} />
      </Form>
  );
}

export const vcsBridgeForm = registerForm(MODEL, VcsBridgeForm);

/**
 * Copy the named keys the form actually submitted. An absent key stays absent so
 * the patch leaves that field `UNSET` server-side rather than clearing it.
 */
function pickPresent(
  data: Record<string, unknown>,
  names: readonly string[],
): Record<string, unknown> {
  const picked: Record<string, unknown> = {};
  for (const name of names) {
    if (data[name] !== undefined) picked[name] = data[name];
  }
  return picked;
}
