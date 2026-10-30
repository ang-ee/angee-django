import type { ReactElement } from "react";
import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";

const VAULT_MODEL = "knowledge.Vault";

/** The shared vault form keeps its title/description creation fields and ordinary saved-record fields. */
function VaultForm({ resource: _resource, children, ...props }: RegisteredFormProps): ReactElement {
  return (
    <Form {...props} resource={VAULT_MODEL}>
      {props.id == null ? <>
        <Field name="name" title />
        <Field name="description" widget="textarea" />
      </> : children}
    </Form>
  );
}

export const vaultCreateForm = registerForm(VAULT_MODEL, VaultForm);
