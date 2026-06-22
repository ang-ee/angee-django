import * as React from "react";
import {
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  RowsListView,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/base";
import { useResourceList, type Row } from "@angee/sdk";

const MODEL = "parties.Person";

// Every resource row selects `id`; narrow to the id-bearing shape RowsListView keys on.
type RelatedRow = Row & { id: string };

const handleColumns: readonly ListColumn<RelatedRow>[] = [
  { field: "platform" },
  { field: "value", render: (row) => <span className="font-medium text-fg">{String(row.value ?? "")}</span> },
  { field: "label" },
  { field: "isPreferred", header: "Preferred", render: (row) => (row.isPreferred ? "Yes" : "") },
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
    field: "organizationName",
    header: "Organization",
    render: (row) => <span className="font-medium text-fg">{String(row.organizationName ?? "")}</span>,
  },
  { field: "title" },
  { field: "role" },
  { field: "department" },
];

/**
 * One related collection on the Person detail — the person's handles, addresses,
 * or affiliations — composed on the shared RowsListView (filtered to this party),
 * never a hand-rolled list, so it inherits the toolbar/empty/error affordances.
 */
function PartyRelatedTab({
  recordId,
  model,
  fields,
  columns,
  emptyMessage,
}: RecordPanelContext & {
  model: string;
  fields: readonly string[];
  columns: readonly ListColumn<RelatedRow>[];
  emptyMessage: string;
}): React.ReactElement {
  const { rows, fetching, error } = useResourceList(model, {
    filter: { party: { sqid: recordId } },
    fields,
  });
  return (
    <RowsListView
      rows={rows as readonly RelatedRow[]}
      columns={columns}
      fetching={fetching}
      error={error}
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
        model="parties.Handle"
        fields={["id", "platform", "value", "label", "isPreferred"]}
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
        model="parties.Address"
        fields={["id", "label", "street", "city", "region", "postalCode", "country"]}
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
        model="parties.Affiliation"
        fields={["id", "organizationName", "title", "role", "department"]}
        columns={affiliationColumns}
        emptyMessage="No affiliations for this contact yet."
      />
    ),
  },
];

const peopleForm = (
  <Form model={MODEL}>
    <Field name="displayName" title />
    <Group label="Name" columns={2}>
      <Field name="givenName" label="Given name" />
      <Field name="familyName" label="Family name" />
      <Field name="additionalName" label="Middle name" />
      <Field name="nickname" label="Nickname" />
      <Field name="namePrefix" label="Prefix" />
      <Field name="nameSuffix" label="Suffix" />
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
    <DataPage model={MODEL} placement="inline" routed recordTabs={personRecordTabs}>
      <List model={MODEL}>
        <Column field="displayName" />
        <Column field="folder.name" header="Folder" />
        <Column field="givenName" />
        <Column field="familyName" />
        <Column field="createdAt" />
      </List>
      {peopleForm}
    </DataPage>
  );
}
