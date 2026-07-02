import * as React from "react";
import { Column, ResourceList, Facet, Field, Form, Group, ListView, List, type ListColumn, type RecordPanelContext, type RecordTabDescriptor, type StringIdRow } from "@angee/ui";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Person";

type RelatedRow = StringIdRow;

function handleColumns(t: ReturnType<typeof usePartiesT>): readonly ListColumn<RelatedRow>[] {
  return [
    { field: "platform" },
    { field: "value", render: (row) => <span className="font-medium text-fg">{String(row.value ?? "")}</span> },
    { field: "label" },
    {
      field: "is_preferred",
      header: t("person.handlePreferred"),
      render: (row) => (row.is_preferred ? t("common.yes") : ""),
    },
  ];
}

const addressColumns: readonly ListColumn<RelatedRow>[] = [
  { field: "label" },
  { field: "street", render: (row) => <span className="font-medium text-fg">{String(row.street ?? "")}</span> },
  { field: "city" },
  { field: "region" },
  { field: "country" },
];

function affiliationColumns(t: ReturnType<typeof usePartiesT>): readonly ListColumn<RelatedRow>[] {
  return [
    {
      field: "organization_name",
      header: t("person.affiliationOrganization"),
      render: (row) => <span className="font-medium text-fg">{String(row.organization_name ?? "")}</span>,
    },
    { field: "title" },
    { field: "role" },
    { field: "department" },
  ];
}

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
  emptyContent,
}: RecordPanelContext & {
  resource: string;
  fields: readonly string[];
  columns: readonly ListColumn<RelatedRow>[];
  emptyContent: string;
}): React.ReactElement {
  return (
    <ListView<RelatedRow>
      resource={resource}
      scope="local"
      fields={fields}
      baseFilter={{ party: { _eq: recordId } }}
      columns={columns}
      emptyContent={emptyContent}
    />
  );
}

function personRecordTabs(
  t: ReturnType<typeof usePartiesT>,
): readonly RecordTabDescriptor[] {
  return [
    {
      id: "handles",
      label: t("person.tabs.handles"),
      render: (context) => (
        <PartyRelatedTab
          {...context}
          resource="parties.Handle"
          fields={["id", "platform", "value", "label", "is_preferred"]}
          columns={handleColumns(t)}
          emptyContent={t("person.empty.handles")}
        />
      ),
    },
    {
      id: "addresses",
      label: t("person.tabs.addresses"),
      render: (context) => (
        <PartyRelatedTab
          {...context}
          resource="parties.Address"
          fields={["id", "label", "street", "city", "region", "postal_code", "country"]}
          columns={addressColumns}
          emptyContent={t("person.empty.addresses")}
        />
      ),
    },
    {
      id: "affiliations",
      label: t("person.tabs.affiliations"),
      render: (context) => (
        <PartyRelatedTab
          {...context}
          resource="parties.Affiliation"
          fields={["id", "organization_name", "title", "role", "department"]}
          columns={affiliationColumns(t)}
          emptyContent={t("person.empty.affiliations")}
        />
      ),
    },
  ];
}

function peopleForm(t: ReturnType<typeof usePartiesT>): React.ReactElement {
  return (
    <Form resource={MODEL}>
      <Field name="display_name" title />
      <Group label={t("person.group.name")} columns={2}>
        <Field name="given_name" label={t("person.field.givenName")} />
        <Field name="family_name" label={t("person.field.familyName")} />
        <Field name="additional_name" label={t("person.field.middleName")} />
        <Field name="nickname" label={t("person.field.nickname")} />
        <Field name="name_prefix" label={t("person.field.prefix")} />
        <Field name="name_suffix" label={t("person.field.suffix")} />
      </Group>
      <Group label={t("person.group.details")} columns={2}>
        <Field name="birthday" label={t("person.field.birthday")} />
        <Field name="anniversary" label={t("person.field.anniversary")} />
        <Field name="folder" label={t("person.folder")} readOnly />
      </Group>
      <Field name="notes" />
    </Form>
  );
}

/**
 * People (the person-kind contacts): full create/edit/list/detail, browsable by
 * folder. The folder navigation comes from the shared model-driven list: the
 * visible `folder.name` relation column plus generated metadata supplies the
 * relation facet/group affordances. The detail carries the contact's handles,
 * addresses, and affiliations as tabs.
 */
export function PeoplePage(): React.ReactElement {
  const t = usePartiesT();
  const tabs = React.useMemo(() => personRecordTabs(t), [t]);
  return (
    <ResourceList resource={MODEL} placement="inline" routed recordTabs={tabs}>
      <List resource={MODEL}>
        <Facet field="folder" label={t("person.folder")} labelField="name" />
        <Column field="display_name" />
        <Column field="folder.name" header={t("person.folder")} />
        <Column field="given_name" />
        <Column field="family_name" />
        <Column field="created_at" />
      </List>
      {peopleForm(t)}
    </ResourceList>
  );
}
