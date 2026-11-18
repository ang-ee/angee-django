import { Field, Group, optionToken } from "@angee/ui";
import { type Row } from "@angee/metadata";
import type { ReactElement } from "react";

import { useOidcT } from "../i18n";

// The login tab shows only for the provider types this addon signs users in with:
// its own GenericOidc and integrate_google's Google preset, which this addon
// depends on. Keeping the predicate here, with the login policy, leaves
// integrate's OAuth form login-agnostic (and a project without this addon composes
// with no login tab). Reads tolerate both the lowercase create value and the
// UPPERCASE read casing of the ImplClassField enum.
const OIDC_PROVIDER_TYPES = ["google", "generic_oidc"];

function isOidcProvider(values: Row): boolean {
  return OIDC_PROVIDER_TYPES.includes(optionToken(values.provider_type));
}

/**
 * The OIDC/login section contributed into integrate's OAuth-client form via
 * `integrate.OAuthClient#sections`. It renders as a "Sign-in (OIDC)" tab whose fields all
 * gate on the OIDC provider types, so the tab appears only for a login provider.
 * The fields are native on `OAuthClient` (folded in by the OIDC `extends` model).
 */
function OidcSectionLabel(): ReactElement {
  const t = useOidcT();
  return <>{t("oidc.section")}</>;
}

export const oidcLoginSection = (
  <Group label={<OidcSectionLabel />} columns={2}>
    <Field name="login_enabled" widget="booleanBadge" showWhen={isOidcProvider} />
    <Field name="link_on_email_match" showWhen={isOidcProvider} />
    <Field name="create_on_login" showWhen={isOidcProvider} />
    <Field
      name="allowed_email_domains"
      widget="tagInput"
      showWhen={isOidcProvider}
    />
  </Group>
);
