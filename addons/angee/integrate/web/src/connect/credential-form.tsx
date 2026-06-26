import type { ReactNode } from "react";
import { Field } from "@angee/ui";

/**
 * The declarative create form for integrate credentials, registered as the
 * `Credential` form override so it is used wherever a credential is created — the
 * Credentials page "New" and the relation-picker inline create (e.g. an
 * Integration's credential field).
 *
 * A `kind` discriminator swaps the material field via `showWhen` (static token →
 * API token; SSH key → private key); the secret is write-only and `user` defaults
 * to the session admin server-side. OAuth credentials are minted by the connect
 * flow, so they are not offered here.
 */
export const credentialCreateForm: ReactNode = (
  <>
    <Field name="name" title placeholder="e.g. GitHub PAT" />
    <Field
      name="kind"
      label="Kind"
      widget="select"
      options={[
        { value: "static_token", label: "Static token" },
        { value: "ssh_key", label: "SSH key" },
      ]}
    />
    <Field
      name="apiKey"
      label="API token"
      widget="text"
      kind="string"
      placeholder="Paste the static token"
      showWhen={(values) => values.kind === "static_token"}
    />
    <Field
      name="privateKey"
      label="Private key"
      widget="textarea"
      kind="string"
      placeholder="Paste the private key (PEM)"
      showWhen={(values) => values.kind === "ssh_key"}
    />
  </>
);
