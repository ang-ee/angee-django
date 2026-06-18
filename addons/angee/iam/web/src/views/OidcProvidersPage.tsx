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
  IAM_DISCOVER_OIDC_ENDPOINTS_MUTATION,
  type DiscoverOidcEndpointsData,
  type IamIdVariables,
} from "../documents";
import { useIamT } from "../i18n";

// OIDC login providers are OAuth clients with the login fields set. The OIDC
// addon folds issuer/JWKS/login policy onto `OAuthClient`, so this page edits the
// OAuth client's login fields directly (new rows default to a login provider).
const MODEL = "OAuthClient";

/**
 * OIDC sign-in providers — the login-enabled OAuth clients (`@angee/integrate` owns
 * the OAuth base; `@angee/iam` owns "what logs users in"). `discover` fills the
 * endpoints (authorize/token/userinfo) and the OIDC issuer/JWKS from the issuer's
 * discovery document onto the one client row.
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

  // Lists every OAuth client and edits its OIDC/login fields; new rows default to a
  // login provider. (A login-enabled-only filter/grouping awaits a filterable
  // `oauthClients` query — the integrate console query is currently unfiltered.)
  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      createDefaults={{ loginEnabled: true }}
    >
      <List model={MODEL}>
        <Column field="displayName" header={t("iam.oidc.column.provider")} />
        <Column
          field="isEnabled"
          header={t("iam.oidc.column.status")}
          widget="booleanBadge"
        />
        <Column field="discoveryUrl" />
        <Column field="issuer" />
        <Column field="linkOnEmailMatch" />
        <Column field="createOnLogin" />
      </List>
      <Form model={MODEL}>
        <Field name="displayName" title />
        <Group label={t("iam.oidc.group.provider")} columns={2}>
          <Field name="isEnabled" widget="booleanBadge" />
          <Field name="loginEnabled" widget="booleanBadge" />
          <Field name="discoveryUrl" />
          <Field name="issuer" />
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
