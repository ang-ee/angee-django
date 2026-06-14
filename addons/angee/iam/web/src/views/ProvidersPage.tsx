import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  type ActionContext,
} from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";

import {
  CONNECT_ACCOUNT_COMPLETE_MUTATION,
  CONNECT_ACCOUNT_START_MUTATION,
  IAM_DISCOVER_OIDC_ENDPOINTS_MUTATION,
  type ConnectAccountCompleteData,
  type ConnectAccountCompleteVariables,
  type ConnectAccountStartData,
  type ConnectAccountStartVariables,
  type DiscoverOidcEndpointsData,
  type IamIdVariables,
} from "../documents";
import { connectCallbackRedirectUri } from "../redirects";

const MODEL = "OAuthClient";

const providerList = (
  <List model={MODEL} pageSize={50}>
    <Column field="slug" />
    <Column field="displayName" />
    <Column field="environment" />
    <Column field="isOidc" />
    <Column field="isEnabled" />
    <Column field="configurationState" />
  </List>
);

function canConnectAccount(record: Record<string, unknown>): boolean {
  if (record.isEnabled !== true) return false;
  if (!fieldString(record.clientId)) return false;
  return Boolean(
    fieldString(record.discoveryUrl) ||
      (fieldString(record.authorizeEndpoint) && fieldString(record.tokenEndpoint)),
  );
}

function fieldString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** OAuth/OIDC login providers (full CRUD plus enable/disable and discovery). */
export function ProvidersPage(): React.ReactElement {
  const [discoverEndpoints] = useAuthoredMutation<
    DiscoverOidcEndpointsData,
    IamIdVariables
  >(IAM_DISCOVER_OIDC_ENDPOINTS_MUTATION);
  const [connectAccountStart] = useAuthoredMutation<
    ConnectAccountStartData,
    ConnectAccountStartVariables
  >(CONNECT_ACCOUNT_START_MUTATION);
  const [connectAccountComplete] = useAuthoredMutation<
    ConnectAccountCompleteData,
    ConnectAccountCompleteVariables
  >(CONNECT_ACCOUNT_COMPLETE_MUTATION);

  const discover = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await discoverEndpoints({ id: ctx.record.id });
      ctx.refresh();
      return result?.discoverOidcEndpoints.message;
    },
    [discoverEndpoints],
  );

  const connect = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof window === "undefined" || typeof ctx.record?.id !== "string") {
        return;
      }
      const result = await connectAccountStart({
        id: ctx.record.id,
        redirectUri: connectCallbackRedirectUri(),
        next: "/iam/accounts",
      });
      const payload = result?.connectAccountStart;
      if (!payload?.authorizeUrl) {
        throw new Error(payload?.error ?? "Could not start account connection.");
      }
      if (payload.mode !== "manual") {
        // Redirect-back flow: the browser leaves and the callback page completes.
        window.location.assign(payload.authorizeUrl);
        return "Redirecting...";
      }
      // Manual flow: the provider only displays the code (no redirect back), so keep
      // this tab/session alive and let the user paste the `code#state` it shows. The
      // link is a real user gesture, so it is never popup-blocked.
      const entered = await ctx.prompt({
        title: "Connect account",
        body: (
          <span>
            <a
              href={payload.authorizeUrl}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              Open the authorization page
            </a>
            , approve, then paste the code it shows below.
          </span>
        ),
        fields: [
          { name: "pasted", label: "Authorization code", placeholder: "code#state" },
        ],
      });
      if (!entered) return;
      // Split on the LAST "#": the state tail is a urlsafe token (no "#"), but an
      // opaque authorization code may contain one — anchoring on the end keeps it whole.
      const pasted = (entered.pasted ?? "").trim();
      const hash = pasted.lastIndexOf("#");
      const code = hash > 0 ? pasted.slice(0, hash) : "";
      const pastedState = hash > 0 ? pasted.slice(hash + 1) : "";
      if (!code || !pastedState) {
        throw new Error("That code looks incomplete — paste the full value the page showed.");
      }
      if (payload.state && pastedState !== payload.state) {
        throw new Error("That code is from a different attempt — start the connection again.");
      }
      if (!payload.redirectUri) {
        throw new Error("Connection state is incomplete — start the connection again.");
      }
      const completed = await connectAccountComplete({
        code,
        state: pastedState,
        redirectUri: payload.redirectUri,
      });
      const done = completed?.connectAccountComplete;
      if (done?.error) {
        throw new Error(done.error);
      }
      ctx.refresh();
      return "Account connected.";
    },
    [connectAccountStart, connectAccountComplete],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {providerList}
      <Form model={MODEL}>
        <Field name="displayName" title />
        <Group label="Client" columns={2}>
          <Field name="slug" />
          <Field name="icon" />
          <Field name="environment" />
          <Field name="clientId" />
          <Field name="clientSecret" />
        </Group>
        <Group label="Endpoints" columns={2}>
          <Field name="issuer" />
          <Field name="discoveryUrl" />
          <Field name="authorizeEndpoint" />
          <Field name="tokenEndpoint" />
          <Field name="tokenRequestFormat" />
          <Field name="userinfoEndpoint" />
          <Field name="jwksUri" />
          <Field name="revokeEndpoint" />
          <Field name="manualRedirectUri" />
        </Group>
        <Group label="Login policy" columns={2}>
          <Field name="isOidc" />
          <Field name="isEnabled" />
          <Field name="linkOnEmailMatch" />
          <Field name="createOnLogin" />
        </Group>
        <Group label="Scopes" columns={2}>
          <Field name="defaultScopes" widget="tagInput" />
          <Field name="scopesCatalogue" widget="tagInput" />
          <Field name="allowedEmailDomains" widget="tagInput" />
        </Group>
        <Group label="OAuth metadata" columns={2}>
          <Field name="authorizeParams" />
          <Field name="tokenParams" />
          <Field name="externalIdClaim" />
          <Field name="emailClaim" />
          <Field name="displayNameClaim" />
          <Field name="avatarUrlClaim" />
        </Group>
        <Action
          id="connect"
          label="Connect account"
          run={connect}
          visibleWhen={canConnectAccount}
        />
        <Action id="discover" label="Discover endpoints" run={discover} />
        <Action
          id="disable"
          label="Disable"
          danger
          set={{ isEnabled: false }}
          visibleWhen={(record) => record.isEnabled === true}
        />
        <Action
          id="enable"
          label="Enable"
          set={{ isEnabled: true }}
          visibleWhen={(record) => record.isEnabled === false}
        />
      </Form>
    </DataPage>
  );
}
