import { type ReactElement } from "react";

import {
  Column,
  DrawerDataPage,
  Field,
  Form,
  GroupListView,
  List,
} from "@angee/base";
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
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-6 px-6 py-6 sm:px-8">
      <header className="grid gap-0.5">
        <h2 className="text-15 font-semibold text-fg">
          {t("knowledge.settings.title")}
        </h2>
        <p className="text-13 text-fg-muted">
          {t("knowledge.settings.description")}
        </p>
      </header>
      <DrawerDataPage model={VAULT_MODEL}>
        <List model={VAULT_MODEL} list={GroupListView} order={{ name: "ASC" }}>
          <Column field="name" />
          <Column field="owner_label" />
          <Column field="updated_at" />
        </List>
        <Form model={VAULT_MODEL}>
          <Field name="name" widget="text" title />
          <Field name="description" widget="textarea" />
          <Field name="icon" />
          <Field name="accent" />
        </Form>
      </DrawerDataPage>
    </div>
  );
}
