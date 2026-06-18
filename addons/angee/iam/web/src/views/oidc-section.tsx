import { Field, Group } from "@angee/base";
import type { Row } from "@angee/sdk";

// iam owns the OIDC provider-type impls (GenericOidc, Google). The tab those impls
// enable shows only when the OAuth client's provider_type is one of them — keeping
// the predicate here, with the addon that owns the impls, leaves integrate's OAuth
// form OIDC-agnostic (and a project without this addon composes with no OIDC tab).
// Reads tolerate both the lowercase create value and the UPPERCASE read casing of
// the ImplClassField enum.
const OIDC_PROVIDER_TYPES = ["google", "generic_oidc"];

function isOidcProvider(values: Row): boolean {
  return OIDC_PROVIDER_TYPES.includes(
    String(values.providerType ?? "").trim().toLowerCase(),
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
    <Field name="loginEnabled" widget="booleanBadge" showWhen={isOidcProvider} />
    <Field name="issuer" showWhen={isOidcProvider} />
    <Field name="jwksUri" showWhen={isOidcProvider} />
    <Field name="linkOnEmailMatch" showWhen={isOidcProvider} />
    <Field name="createOnLogin" showWhen={isOidcProvider} />
    <Field
      name="allowedEmailDomains"
      widget="tagInput"
      showWhen={isOidcProvider}
    />
  </Group>
);
