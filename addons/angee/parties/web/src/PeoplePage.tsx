import * as React from "react";
import {
  Column,
  ResourceList,
  Facet,
  Field,
  Form,
  Group,
  ListView,
  List,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";

const MODEL = "parties.Person";

type RelatedRow = StringIdRow;

const handleColumns: readonly ListColumn<RelatedRow>[] = [
  { field: "platform" },
  { field: "value", render: (row) => <span className="font-medium text-fg">{String(row.value ?? "")}</span> },
  { field: "label" },
  { field: "is_preferred", header: "Preferred", render: (row) => (row.is_preferred ? "Yes" : "") },
];

const addressColumns: readonly ListColumn<RelatedRow>[] = [
  { field: "label" },
  { field: "street", render: (row) => <span className="font-medium text-fg">{String(row.street ?? "")}</span> },
  { field: "city" },
  { field: "region" },
  { field: "country" },
];

const affiliationColumns: readonly ListColumn<RelatedRow>[] = [
  {
    field: "organization_name",
    header: "Organization",
    render: (row) => <span className="font-medium text-fg">{String(row.organization_name ?? "")}</span>,
  },
  { field: "title" },
  { field: "role" },
  { field: "department" },
];

/**
 * One related collection on the Person detail — the person's handles, addresses,
 * or affiliations — a local-scoped ListView filtered to this party, the same
 * shared list primitive the routed pages use (toolbar/empty/error affordances).
 */
function PartyRelatedTab({
  recordId,
  resource,
  fields,
  columns,
  emptyMessage,
}: RecordPanelContext & {
  resource: string;
  fields: readonly string[];
  columns: readonly ListColumn<RelatedRow>[];
  emptyMessage: string;
}): React.ReactElement {
  return (
    <ListView<RelatedRow>
      resource={resource}
      scope="local"
      fields={fields}
      filter={{ party: { _eq: recordId } }}
      columns={columns}
      emptyMessage={emptyMessage}
    />
  );
}

const personRecordTabs: readonly RecordTabDescriptor[] = [
  {
    id: "handles",
    label: "Handles",
    render: (context) => (
      <PartyRelatedTab
        {...context}
        resource="parties.Handle"
        fields={["id", "platform", "value", "label", "is_preferred"]}
        columns={handleColumns}
        emptyMessage="No handles for this contact yet."
      />
    ),
  },
  {
    id: "addresses",
    label: "Addresses",
    render: (context) => (
      <PartyRelatedTab
        {...context}
        resource="parties.Address"
        fields={["id", "label", "street", "city", "region", "postal_code", "country"]}
        columns={addressColumns}
        emptyMessage="No addresses for this contact yet."
      />
    ),
  },
  {
    id: "affiliations",
    label: "Affiliations",
    render: (context) => (
      <PartyRelatedTab
        {...context}
        resource="parties.Affiliation"
        fields={["id", "organization_name", "title", "role", "department"]}
        columns={affiliationColumns}
        emptyMessage="No affiliations for this contact yet."
      />
    ),
  },
];

const peopleForm = (
  <Form resource={MODEL}>
    <Field name="display_name" title />
    <Group label="Name" columns={2}>
      <Field name="given_name" label="Given name" />
      <Field name="family_name" label="Family name" />
      <Field name="additional_name" label="Middle name" />
      <Field name="nickname" label="Nickname" />
      <Field name="name_prefix" label="Prefix" />
      <Field name="name_suffix" label="Suffix" />
    </Group>
    <Group label="Details" columns={2}>
      <Field name="birthday" label="Birthday" />
      <Field name="anniversary" label="Anniversary" />
      <Field name="folder" label="Folder" readOnly />
    </Group>
    <Field name="notes" />
  </Form>
);

/**
 * People (the person-kind contacts): full create/edit/list/detail, browsable by
 * folder. The folder navigation comes from the shared model-driven list: the
 * visible `folder.name` relation column plus generated metadata supplies the
 * relation facet/group affordances. The detail carries the contact's handles,
 * addresses, and affiliations as tabs.
 */
export function PeoplePage(): React.ReactElement {
  return (
    <ResourceList resource={MODEL} placement="inline" routed recordTabs={personRecordTabs}>
      <List resource={MODEL}>
        <Facet field="folder" label="Folder" labelField="name" />
        <Column field="display_name" />
        <Column field="folder.name" header="Folder" />
        <Column field="given_name" />
        <Column field="family_name" />
        <Column field="created_at" />
      </List>
      {peopleForm}
    </ResourceList>
  );
}
