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
  type DataToolbarGroupOption,
} from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";

import {
  IAM_DISCOVER_OIDC_ENDPOINTS_MUTATION,
  type DiscoverOidcEndpointsData,
  type IamIdVariables,
} from "../documents";
import { useIamT } from "../i18n";

const MODEL = "OidcClient";

/**
 * OIDC sign-in providers — the login refinement of an OAuth client (`@angee/integrate`
 * owns the OAuth base). Inbound auth lives here ("what logs users in"); the row is the
 * `OidcClient` 1:1 refinement (issuer/JWKS/discovery + login policy), and `discover`
 * fills the endpoints across the client and its refinement from the issuer's metadata.
 * A provider is only *live* when its OAuth client is enabled, so that state
 * (`oauthEnabled`, resolved from the related client) shows as an Enabled/Disabled pill
 * in the list and on the detail.
 */
export function OidcProvidersPage(): React.ReactElement {
  const t = useIamT();
  const [discoverEndpoints] = useAuthoredMutation<
    DiscoverOidcEndpointsData,
    IamIdVariables
  >(IAM_DISCOVER_OIDC_ENDPOINTS_MUTATION);

  const discover = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await discoverEndpoints({ id: ctx.record.id });
      ctx.refresh();
      return result?.discoverOidcEndpoints.message;
    },
    [discoverEndpoints],
  );

  // The OAuth client's enabled flag, surfaced as an Enabled/Disabled pill
  // (`booleanBadge` tones it success/neutral). Shared by the list column and the
  // read-only detail field so a provider's live state is unambiguous.
  const statusOptions = React.useMemo(
    () => [
      { value: "true", label: t("iam.oidc.status.enabled") },
      { value: "false", label: t("iam.oidc.status.disabled") },
    ],
    [t],
  );

  // Group providers by their OAuth client's enabled flag. The axis is the
  // 1:1 relation path `oauth_client__is_enabled`, surfaced on the group key as
  // `oauthClient_IsEnabled` (its camel form) — the value the toolbar group menu
  // round-trips to the backend enum.
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () => [
      {
        id: "oauthEnabled",
        label: t("iam.oidc.column.status"),
        group: { field: "oauthClient_IsEnabled" },
        type: "value",
      },
    ],
    [t],
  );

  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      groupOptions={groupOptions}
    >
      <List model={MODEL}>
        <Column field="oauthClient.displayName" header={t("iam.oidc.column.provider")} />
        <Column
          field="oauthEnabled"
          header={t("iam.oidc.column.status")}
          widget="booleanBadge"
          options={statusOptions}
        />
        <Column field="discoveryUrl" />
        <Column field="issuer" />
        <Column field="linkOnEmailMatch" />
        <Column field="createOnLogin" />
      </List>
      <Form model={MODEL}>
        <Field name="issuer" title />
        <Group label={t("iam.oidc.group.provider")} columns={2}>
          {/* The refined OAuth client is fixed at creation (absent from the patch),
              so it is a select-existing many2one, locked on edit. Its enabled state
              is read-only here — change it on the OAuth client (follow the arrow). */}
          <Field name="oauthClient" widget="many2one" createOnly />
          <Field
            name="oauthEnabled"
            label={t("iam.oidc.column.status")}
            widget="booleanBadge"
            options={statusOptions}
            readOnly
          />
          <Field name="discoveryUrl" />
          <Field name="jwksUri" />
        </Group>
        <Group label={t("iam.oidc.group.loginPolicy")} columns={2}>
          <Field name="linkOnEmailMatch" />
          <Field name="createOnLogin" />
          <Field name="allowedEmailDomains" widget="tagInput" />
        </Group>
        <Action
          id="discover"
          label={t("iam.oidc.action.discover")}
          run={discover}
        />
      </Form>
    </DataPage>
  );
}
