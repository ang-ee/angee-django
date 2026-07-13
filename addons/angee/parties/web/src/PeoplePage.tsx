import * as React from "react";
import { Column, ResourceList, Facet, Field, Form, Group, ListView, List, type ListColumn, type RecordPanelContext, type RecordTabDescriptor, type StringIdRow } from "@angee/ui";
import { IdentityTab } from "./IdentityTab";
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
      baseFilter={{ party: { exact: recordId } }}
      columns={columns}
      emptyContent={emptyContent}
    />
  );
}

function circleMembershipColumns(t: ReturnType<typeof usePartiesT>): readonly ListColumn<RelatedRow>[] {
  return [
    { field: "circle.name", header: t("person.circleName") },
    { field: "source" },
    { field: "confidence" },
  ];
}

/**
 * Both readings of the person's typed edges in one tab: rows anchored on this
 * card (the counterparty is their *kind* — "Mother: Jane", including free-text
 * relatives who are not directory entries) and rows anchored on other cards
 * that name this person (rendered through the kind's inverse label, falling
 * back to the forward name for symmetric kinds).
 */
function RelationshipsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = usePartiesT();
  const anchoredColumns = React.useMemo<readonly ListColumn<RelatedRow>[]>(
    () => [
      { field: "kind.name", header: t("relationship.kind") },
      {
        field: "other_party.display_name",
        header: t("relationship.person"),
        render: (row) => {
          const typed = row as { other_party?: { display_name?: string } | null; other_name?: string };
          return <>{typed.other_party?.display_name || typed.other_name || ""}</>;
        },
      },
      { field: "started_at" },
      { field: "ended_at" },
    ],
    [t],
  );
  const inboundColumns = React.useMemo<readonly ListColumn<RelatedRow>[]>(
    () => [
      {
        field: "kind.name",
        header: t("relationship.kind"),
        render: (row) => {
          const kind = (row as { kind?: { name?: string; inverse_name?: string } }).kind;
          return <>{kind?.inverse_name || kind?.name || ""}</>;
        },
      },
      { field: "party.display_name", header: t("relationship.person") },
      { field: "started_at" },
      { field: "ended_at" },
    ],
    [t],
  );
  return (
    <div className="flex flex-col gap-4">
      <ListView<RelatedRow>
        resource="parties.Relationship"
        scope="local"
        fields={["id", "kind.name", "other_party.display_name", "other_name", "started_at", "ended_at"]}
        baseFilter={{ party: { exact: recordId } }}
        columns={anchoredColumns}
        emptyContent={t("person.empty.relationships")}
      />
      <ListView<RelatedRow>
        resource="parties.Relationship"
        scope="local"
        fields={["id", "kind.name", "kind.inverse_name", "party.display_name", "started_at", "ended_at"]}
        baseFilter={{ other_party: { exact: recordId } }}
        columns={inboundColumns}
        emptyContent={t("person.empty.inboundRelationships")}
      />
    </div>
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
      id: "identity",
      label: t("person.tabs.identity"),
      render: (context) => <IdentityTab {...context} />,
    },
    {
      id: "circles",
      label: t("person.tabs.circles"),
      render: (context) => (
        <PartyRelatedTab
          {...context}
          resource="parties.CircleMember"
          fields={["id", "circle.name", "source", "confidence"]}
          columns={circleMembershipColumns(t)}
          emptyContent={t("person.empty.circles")}
        />
      ),
    },
    {
      id: "relationships",
      label: t("person.tabs.relationships"),
      render: (context) => <RelationshipsTab {...context} />,
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
