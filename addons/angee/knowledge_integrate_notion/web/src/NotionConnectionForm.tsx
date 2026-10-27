import type { ReactElement } from "react";
import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields, useIntegrationSyncAction } from "@angee/integrate";

import { useNotionT } from "./i18n";

export const NOTION_CONNECTION_MODEL = "knowledge_integrate_notion.NotionConnection";

/** Compose credentials, lifecycle actions and synchronization from their shared owners. */
function NotionConnectionForm({ resource: _resource, ...props }: RegisteredFormProps): ReactElement {
  const t = useNotionT();
  const sync = useIntegrationSyncAction("sync_notion_connection", t("action.sync"));
  return (
    <Form {...props} resource={NOTION_CONNECTION_MODEL}>
      <Field name="display_name" title />
      <Field name="owner" />
      <Field name="credential" />
      <Field name="vault" readOnly />
      <Field name="rewrite_page_links" />
      <Field name="lifecycle" widget="statusbar" status readOnly />
      <Field name="runtime_status" widget="statusBadge" readOnly />
      <Field name="last_error" readOnly />
      {IntegrationSyncFields({ label: t("group.sync") })}
      {sync}
    </Form>
  );
}

export const notionConnectionForm = registerForm(NOTION_CONNECTION_MODEL, NotionConnectionForm);
