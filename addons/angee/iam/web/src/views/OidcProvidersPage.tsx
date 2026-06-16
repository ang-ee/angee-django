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

const MODEL = "OidcClient";

const oidcList = (
  <List model={MODEL}>
    <Column field="discoveryUrl" />
    <Column field="issuer" />
    <Column field="linkOnEmailMatch" />
    <Column field="createOnLogin" />
  </List>
);

/**
 * OIDC sign-in providers — the login refinement of an OAuth client (`@angee/integrate`
 * owns the OAuth base). Inbound auth lives here ("what logs users in"); the row is the
 * `OidcClient` 1:1 refinement (issuer/JWKS/discovery + login policy), and `discover`
 * fills the endpoints across the client and its refinement from the issuer's metadata.
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

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {oidcList}
      <Form model={MODEL}>
        <Field name="issuer" title />
        <Group label={t("iam.oidc.group.provider")} columns={2}>
          {/* The refined OAuth client is fixed at creation (absent from the patch),
              so it is a select-existing many2one, locked on edit. */}
          <Field name="oauthClient" widget="many2one" createOnly />
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
