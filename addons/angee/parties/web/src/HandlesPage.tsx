import * as React from "react";
import { Column, ResourceList, Field, Form, Group, List } from "@angee/ui";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Handle";

/**
 * Handles: a flat, searchable list of every contact point (emails, phones, social
 * handles) across all contacts. The model-driven list derives the per-platform
 * facet from the enum column. Handles are created and maintained through contacts
 * and directory sync, so the surface is browse-only (`hideCreate`, read-only
 * detail) — the deferred standalone view of the rows that also appear on each
 * Person's Handles tab.
 */
export function HandlesPage(): React.ReactElement {
  const t = usePartiesT();
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate>
      <List resource={MODEL}>
        <Column field="value" />
        <Column field="platform" />
        <Column field="label" />
        <Column field="party.display_name" header={t("handle.contact")} />
        <Column field="confidence" header={t("handle.confidence")} />
        <Column field="is_preferred" header={t("handle.preferred")} />
      </List>
      <Form resource={MODEL}>
        <Field name="value" title readOnly />
        <Group label={t("handle.group.about")} columns={2}>
          <Field name="platform" readOnly />
          <Field name="label" readOnly />
          <Field name="display_name" readOnly />
          <Field name="party" label={t("handle.contact")} readOnly />
        </Group>
        <Group label={t("handle.group.flags")} columns={3}>
          <Field name="is_preferred" readOnly />
          <Field name="is_own" readOnly />
          <Field name="is_verified" readOnly />
        </Group>
      </Form>
    </ResourceList>
  );
}
