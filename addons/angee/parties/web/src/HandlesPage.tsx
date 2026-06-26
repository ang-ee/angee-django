import * as React from "react";
import {
  Column,
  ResourceList,
  Field,
  Form,
  Group,
  List,
} from "@angee/ui";

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
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate>
      <List resource={MODEL}>
        <Column field="value" />
        <Column field="platform" />
        <Column field="label" />
        <Column field="party.display_name" header="Contact" />
        <Column field="confidence" header="Confidence" />
        <Column field="is_preferred" header="Preferred" />
      </List>
      <Form resource={MODEL}>
        <Field name="value" title readOnly />
        <Group label="About" columns={2}>
          <Field name="platform" readOnly />
          <Field name="label" readOnly />
          <Field name="display_name" readOnly />
          <Field name="party" label="Contact" readOnly />
        </Group>
        <Group label="Flags" columns={3}>
          <Field name="is_preferred" readOnly />
          <Field name="is_own" readOnly />
          <Field name="is_verified" readOnly />
        </Group>
      </Form>
    </ResourceList>
  );
}
