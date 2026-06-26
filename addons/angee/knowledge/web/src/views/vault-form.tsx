import type { ReactNode } from "react";
import { Field } from "@angee/ui";

/**
 * The declarative create form for a knowledge vault, registered as the `Vault`
 * form override so it is used wherever a vault is created — the relation-picker
 * inline create in the wiki navigator (and any future "New vault" surface).
 *
 * `name` is the record title (prefilled with the typed query); creation is gated
 * server-side by `createVault`. Labels fall back to the SDL field metadata, so
 * neither field hard-codes its label.
 */
export const vaultCreateForm: ReactNode = (
  <>
    <Field name="name" title />
    <Field name="description" widget="textarea" />
  </>
);
