import { type ReactElement } from "react";

import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  List,
  SettingsSection,
  SettingsShell,
} from "@angee/ui";
import { useKnowledgeT } from "../i18n";

const VAULT_MODEL = "knowledge.Vault";

/**
 * The knowledge admin console: a managed list of vaults whose record form opens
 * in a drawer. Create / edit / delete are gated server-side. Vaults carry no
 * immutable fields, so the form is plain full CRUD.
 */
export function KnowledgeSettingsPage(): ReactElement {
  const t = useKnowledgeT();
  return (
    <SettingsShell maxWidth="1100" gap="6">
      <SettingsSection
        title={t("knowledge.settings.title")}
        description={t("knowledge.settings.description")}
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
        </Form>
      </DrawerResourceList>
    </SettingsShell>
  );
}
