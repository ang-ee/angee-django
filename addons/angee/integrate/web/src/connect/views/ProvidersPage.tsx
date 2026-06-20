import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  recordActionId,
  useEnumOptions,
  useImplPrefill,
  useRecordActionMutation,
  type ActionContext,
} from "@angee/base";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { useAuthoredMutation } from "@angee/sdk";

import { useIntegrateT } from "../../i18n";
import {
  IntegrateConnectAccountComplete,
  IntegrateConnectAccountStart,
} from "../documents.public";
import { parseManualCode } from "../ConnectOAuthButton";
import { connectCallbackRedirectUri } from "../redirects";

const MODEL = "OAuthClient";

const providerList = (
  <List model={MODEL}>
    <Column field="slug" />
    <Column field="displayName" />
    <Column field="environment" />
    <Column field="isEnabled" />
    <Column field="configurationState" />
  </List>
);

// Connect-readiness is an OAuth concern: an enabled client with credentials and an
// authorize/token endpoint pair. Discovery can fill those endpoints, but the
// actual connect flow requires the resolved transport fields.
function canConnectAccount(record: Record<string, unknown>): boolean {
  if (record.isEnabled !== true) return false;
  if (!fieldString(record.clientId)) return false;
  return Boolean(
    fieldString(record.authorizeEndpoint) && fieldString(record.tokenEndpoint),
  );
}

function fieldString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** OAuth connect providers (full CRUD plus enable/disable and account connect). */
export function ProvidersPage(): React.ReactElement {
  const t = useIntegrateT();
  const [connectAccountStart] = useAuthoredMutation(IntegrateConnectAccountStart);
  const [connectAccountComplete] = useAuthoredMutation(
    IntegrateConnectAccountComplete,
  );
  const [discover] = useRecordActionMutation<ActionFieldName>(
    "discoverOauthEndpoints",
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
      const payload = result?.connectAccountStart;
      if (!payload?.authorizeUrl) {
        throw new Error(payload?.error ?? t("integrate.providers.connect.startError"));
      }
      if (payload.mode !== "manual") {
        // Redirect-back flow: the browser leaves and the callback page completes.
        window.location.assign(payload.authorizeUrl);
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
              href={payload.authorizeUrl}
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
      if (!payload.redirectUri) {
        throw new Error(t("integrate.providers.connect.stateIncomplete"));
      }
      const completed = await connectAccountComplete({
        code,
        state,
        redirectUri: payload.redirectUri,
      });
      const done = completed?.connectAccountComplete;
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
  const providerTypeOptions = useEnumOptions(MODEL, "providerType");
  const providerTypePrefill = useImplPrefill(MODEL, "providerType");

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {providerList}
      <Form model={MODEL} layout="tabs">
        <Field name="displayName" title />
        <Group label={t("integrate.providers.group.client")} columns={2}>
          <Field
            name="providerType"
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
          <Field name="clientId" />
          <Field name="clientSecret" />
        </Group>
        <Group label={t("integrate.providers.group.endpoints")} columns={2}>
          <Field name="discoveryUrl" />
          <Field name="authorizeEndpoint" />
          <Field name="tokenEndpoint" />
          <Field name="revokeEndpoint" />
          <Field name="userinfoEndpoint" />
          <Field name="tokenRequestFormat" />
          <Field name="manualRedirectUri" />
        </Group>
        <Group label={t("integrate.providers.group.behavior")} columns={2}>
          <Field name="isEnabled" />
          <Field name="supportsRefresh" />
          <Field name="refreshRotates" />
          <Field name="supportsPkce" />
          <Field name="maxRefreshAgeSeconds" />
        </Group>
        <Group label={t("integrate.providers.group.scopes")} columns={2}>
          <Field name="defaultScopes" widget="tagInput" />
          <Field name="scopesCatalogue" widget="tagInput" />
        </Group>
        <Group label={t("integrate.providers.group.claims")} columns={2}>
          <Field name="externalIdClaim" />
          <Field name="emailClaim" />
          <Field name="displayNameClaim" />
          <Field name="avatarUrlClaim" />
        </Group>
        <Group label={t("integrate.providers.group.oauthMetadata")} columns={2}>
          <Field name="authorizeParams" />
          <Field name="tokenParams" />
        </Group>
        <Action
          id="discover"
          label={t("integrate.providers.action.discover")}
          run={discover}
          visibleWhen={(record) => fieldString(record.discoveryUrl) !== ""}
        />
        <Action
          id="connect"
          label={t("integrate.providers.action.connect")}
          run={connect}
          visibleWhen={canConnectAccount}
        />
        <Action
          id="disable"
          label={t("integrate.providers.action.disable")}
          danger
          set={{ isEnabled: false }}
          visibleWhen={(record) => record.isEnabled === true}
        />
        <Action
          id="enable"
          label={t("integrate.providers.action.enable")}
          set={{ isEnabled: true }}
          visibleWhen={(record) => record.isEnabled === false}
        />
      </Form>
    </DataPage>
  );
}
