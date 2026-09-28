import type { ActionFieldName } from "@angee/gql/console/actions";
import { useCallback, type ReactElement } from "react";

import {
  Action, Column, DrawerResourceList, Field, Form, List, SettingsSection, SettingsShell,
  useActionOutcomeMutation, type ActionDescriptor } from "@angee/ui";
import { PAGE_READ_MODELS } from "../data/documents";
import { useKnowledgeT } from "../i18n";

const VAULT_MODEL = "knowledge.Vault";

/**
 * The knowledge admin console: a managed list of vaults whose record form opens
 * in a drawer. Create / edit / delete are gated server-side. Vaults carry no
 * immutable fields, so the form is plain full CRUD.
 */
export function KnowledgeSettingsPage(): ReactElement {
  const t = useKnowledgeT();
  const [createFrom] = useActionOutcomeMutation<ActionFieldName>("create_vault_from", {
    idArgument: "template",
    invalidateModels: [VAULT_MODEL, ...PAGE_READ_MODELS],
  });
  const cloneSubmit = useCallback<NonNullable<ActionDescriptor["submit"]>>(
    async (values, context) => {
      const template = context.record?.id;
      if (typeof template !== "string" || !template || typeof values.name !== "string") {
        return { ok: false, message: t("vault.cloneFailed") };
      }
      return createFrom(template, { name: values.name.trim() });
    },
    [createFrom, t],
  );
  return (
    <SettingsShell maxWidth="1100" gap="6">
      <SettingsSection
        title={t("settings.title")}
        description={t("settings.description")}
      />
      <DrawerResourceList resource={VAULT_MODEL}>
        <List resource={VAULT_MODEL} order={{ name: "ASC" }}>
          <Column field="name" />
          <Column field="owner_label" />
          <Column field="updated_at" />
        </List>
        <Form resource={VAULT_MODEL}>
          <Field name="name" widget="text" title />
          <Field name="description" widget="textarea" />
          <Field name="icon" />
          <Field name="accent" />
          <Action
            id="create-from-template"
            label={t("vault.clone")}
            icon="copy"
            args={[{ name: "name", label: t("vault.cloneName"), widget: "text" }]}
            submit={cloneSubmit}
            // Keep the action off the create drawer until a record is loaded.
            visibleWhen={() => true}
          />
        </Form>
      </DrawerResourceList>
    </SettingsShell>
  );
}
