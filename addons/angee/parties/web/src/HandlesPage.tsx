import * as React from "react";
import {
  Column,
  DataPage,
  Field,
  Form,
  Group,
  GroupListView,
  List,
  useRelationFacet,
  type DataToolbarGroupOption,
} from "@angee/base";

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
  const partyFacet = useRelationFacet(MODEL, { field: "party", label: "Contact" });
  const groupOptions = React.useMemo<readonly DataToolbarGroupOption[]>(
    () => (partyFacet.groupOption ? [partyFacet.groupOption] : []),
    [partyFacet.groupOption],
  );
  return (
    <DataPage model={MODEL} placement="inline" routed hideCreate>
      <List model={MODEL} list={GroupListView} groupOptions={groupOptions}>
        <Column field="value" />
        <Column field="platform" />
        <Column field="label" />
        <Column field="party.displayName" header="Contact" />
        <Column field="confidence" header="Confidence" />
        <Column field="isPreferred" header="Preferred" />
      </List>
      <Form model={MODEL}>
        <Field name="value" title readOnly />
        <Group label="About" columns={2}>
          <Field name="platform" readOnly />
          <Field name="label" readOnly />
          <Field name="displayName" readOnly />
          <Field name="party" label="Contact" readOnly />
        </Group>
        <Group label="Flags" columns={3}>
          <Field name="isPreferred" readOnly />
          <Field name="isOwn" readOnly />
          <Field name="isVerified" readOnly />
        </Group>
      </Form>
    </DataPage>
  );
}
