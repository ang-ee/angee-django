import * as React from "react";
import {
  Action,
  Column,
  ResourceList,
  Field,
  Form,
  Group,
  List,
  recordActionId,
  useEnumOptions,
  useImplPrefill,
  useRecordActionMutation,
  useAuthoredMutation,
  type ActionContext,
} from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../../i18n";
import {
  IntegrateConnectAccountComplete,
  IntegrateConnectAccountStart,
} from "../documents.public";
import { parseManualCode } from "../ConnectOAuthButton";
import { connectCallbackRedirectUri } from "../redirects";

const MODEL = "OAuthClient";

const providerList = (
  <List resource={MODEL}>
    <Column field="slug" />
    <Column field="display_name" />
    <Column field="environment" />
    <Column field="is_enabled" />
    <Column field="configuration_state" />
  </List>
);

// Connect-readiness is an OAuth concern: an enabled client with credentials and an
// authorize/token endpoint pair. Discovery can fill those endpoints, but the
// actual connect flow requires the resolved transport fields.
function canConnectAccount(record: Record<string, unknown>): boolean {
  if (record.is_enabled !== true) return false;
  if (!fieldString(record.client_id)) return false;
  return Boolean(
    fieldString(record.authorize_endpoint) && fieldString(record.token_endpoint),
  );
}

function fieldString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** OAuth connect providers (full CRUD plus discovery and account connect). */
export function ProvidersPage(): React.ReactElement {
  const t = useIntegrateT();
  const [connectAccountStart] = useAuthoredMutation(IntegrateConnectAccountStart);
  const [connectAccountComplete] = useAuthoredMutation(
    IntegrateConnectAccountComplete,
  );
  const [discover] = useRecordActionMutation<ActionFieldName>(
    "discover_oauth_endpoints",
    { defaultMessage: t("integrate.providers.discover.done") },
  );

  // Fill the transport endpoints from the client's discovery URL (no manual entry).
  // Persists onto the saved row and re-pulls it, so the form shows the resolved
  // endpoints; available once a discovery URL is set.
  const connect = React.useCallback(
    async (ctx: ActionContext) => {
      const id = recordActionId(ctx);
      if (typeof window === "undefined" || !id) return;
      const result = await connectAccountStart({
        id,
        redirectUri: connectCallbackRedirectUri(),
        next: "/integrate/accounts",
      });
      const payload = result?.connect_account_start;
      if (!payload?.authorize_url) {
        throw new Error(payload?.error ?? t("integrate.providers.connect.startError"));
      }
      if (payload.mode !== "manual") {
        // Redirect-back flow: the browser leaves and the callback page completes.
        window.location.assign(payload.authorize_url);
        return t("integrate.providers.connect.redirecting");
      }
      // Manual flow: the provider only displays the code (no redirect back), so keep
      // this tab/session alive and let the user paste the `code#state` it shows. The
      // link is a real user gesture, so it is never popup-blocked.
      const entered = await ctx.prompt({
        title: t("integrate.providers.action.connect"),
        body: (
          <span>
            <a
              href={payload.authorize_url}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              {t("integrate.providers.connect.openAuthorize")}
            </a>
            {t("integrate.providers.connect.instructions")}
          </span>
        ),
        fields: [
          {
            name: "pasted",
            label: t("integrate.providers.connect.codeLabel"),
            placeholder: t("integrate.providers.connect.codePlaceholder"),
          },
        ],
      });
      if (!entered) return;
      const { code, state } = parseManualCode(entered.pasted, payload.state ?? "", t);
      if (!payload.redirect_uri) {
        throw new Error(t("integrate.providers.connect.stateIncomplete"));
      }
      const completed = await connectAccountComplete({
        code,
        state,
        redirectUri: payload.redirect_uri,
      });
      const done = completed?.connect_account_complete;
      if (done?.error) {
        throw new Error(done.error);
      }
      ctx.refresh();
      return t("integrate.providers.connect.connected");
    },
    [connectAccountStart, connectAccountComplete, t],
  );

  // Provider type is an ImplClassField (Google / Generic OIDC / Generic OAuth);
  // picking one seeds the client's defaults (endpoints/scopes/icon) on create via
  // the server-side ImplDefaultsMixin. useEnumOptions lower-cases the write value.
  const providerTypeOptions = useEnumOptions(MODEL, "provider_type");
  const providerTypePrefill = useImplPrefill(MODEL, "provider_type");

  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      {providerList}
      <Form resource={MODEL} layout="tabs">
        <Field name="display_name" title />
        <Group label={t("integrate.providers.group.client")} columns={2}>
          <Field
            name="provider_type"
            widget="select"
            options={providerTypeOptions}
            prefill={providerTypePrefill}
            createOnly
          />
          {/* Slug widget: derives live from the display name (the `title` field)
              while creating, until edited. Editable on its own afterwards. */}
          <Field name="slug" widget="slug" />
          <Field name="icon" />
          <Field name="environment" />
          <Field name="client_id" />
          <Field name="client_secret" />
        </Group>
        <Group label={t("integrate.providers.group.endpoints")} columns={2}>
          <Field name="discovery_url" />
          <Field name="authorize_endpoint" />
          <Field name="token_endpoint" />
          <Field name="revoke_endpoint" />
          <Field name="userinfo_endpoint" />
          <Field name="token_request_format" />
          <Field name="manual_redirect_uri" />
        </Group>
        <Group label={t("integrate.providers.group.behavior")} columns={2}>
          <Field name="is_enabled" />
          <Field name="supports_refresh" />
          <Field name="refresh_rotates" />
          <Field name="supports_pkce" />
          <Field name="max_refresh_age_seconds" />
        </Group>
        <Group label={t("integrate.providers.group.scopes")} columns={2}>
          <Field name="default_scopes" widget="tagInput" />
          <Field name="scopes_catalogue" widget="tagInput" />
        </Group>
        <Group label={t("integrate.providers.group.claims")} columns={2}>
          <Field name="external_id_claim" />
          <Field name="email_claim" />
          <Field name="display_name_claim" />
          <Field name="avatar_url_claim" />
        </Group>
        <Group label={t("integrate.providers.group.oauthMetadata")} columns={2}>
          <Field name="authorize_params" />
          <Field name="token_params" />
        </Group>
        <Action
          id="discover"
          label={t("integrate.providers.action.discover")}
          run={discover}
          visibleWhen={(record) => fieldString(record.discovery_url) !== ""}
        />
        <Action
          id="connect"
          label={t("integrate.providers.action.connect")}
          run={connect}
          visibleWhen={canConnectAccount}
        />
      </Form>
    </ResourceList>
  );
}
