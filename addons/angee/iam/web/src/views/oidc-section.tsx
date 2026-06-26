import { Field, Group } from "@angee/ui";
import { type Row } from "@angee/resources";

// iam owns the OIDC provider-type impls (GenericOidc, Google). The tab those impls
// enable shows only when the OAuth client's provider_type is one of them — keeping
// the predicate here, with the addon that owns the impls, leaves integrate's OAuth
// form OIDC-agnostic (and a project without this addon composes with no OIDC tab).
// Reads tolerate both the lowercase create value and the UPPERCASE read casing of
// the ImplClassField enum.
const OIDC_PROVIDER_TYPES = ["google", "generic_oidc"];

function isOidcProvider(values: Row): boolean {
  return OIDC_PROVIDER_TYPES.includes(
    String(values.provider_type ?? "").trim().toLowerCase(),
  );
}

/**
 * The OIDC/login section contributed into integrate's OAuth-client form via
 * `FORM_VIEW_SECTIONS_SLOT`. It renders as a "Sign-in (OIDC)" tab whose fields all
 * gate on the OIDC provider types, so the tab appears only for a login provider.
 * The fields are native on `OAuthClient` (folded in by the OIDC `extends` model).
 */
export const oidcLoginSection = (
  <Group label="Sign-in (OIDC)" columns={2}>
    <Field name="login_enabled" widget="booleanBadge" showWhen={isOidcProvider} />
    <Field name="issuer" showWhen={isOidcProvider} />
    <Field name="jwks_uri" showWhen={isOidcProvider} />
    <Field name="link_on_email_match" showWhen={isOidcProvider} />
    <Field name="create_on_login" showWhen={isOidcProvider} />
    <Field
      name="allowed_email_domains"
      widget="tagInput"
      showWhen={isOidcProvider}
    />
  </Group>
);
