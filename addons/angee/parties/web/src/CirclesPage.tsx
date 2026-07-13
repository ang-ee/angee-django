import * as React from "react";
import { Column, Field, Form, Group, List, ListView, ResourceList, type ListColumn, type RecordPanelContext, type RecordTabDescriptor, type StringIdRow } from "@angee/ui";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Circle";

type MemberRow = StringIdRow;

function memberColumns(t: ReturnType<typeof usePartiesT>): readonly ListColumn<MemberRow>[] {
  return [
    { field: "party.display_name", header: t("circle.memberParty") },
    { field: "source" },
    { field: "confidence" },
    { field: "created_at" },
  ];
}

/** The circle's members — a local-scoped ListView filtered to this circle. */
function CircleMembersTab({ recordId, ...context }: RecordPanelContext): React.ReactElement {
  const t = usePartiesT();
  void context;
  return (
    <ListView<MemberRow>
      resource="parties.CircleMember"
      scope="local"
      fields={["id", "party.display_name", "source", "confidence", "created_at"]}
      baseFilter={{ circle: { exact: recordId } }}
      columns={memberColumns(t)}
      emptyContent={t("circle.empty.members")}
    />
  );
}

function circleRecordTabs(t: ReturnType<typeof usePartiesT>): readonly RecordTabDescriptor[] {
  return [
    {
      id: "members",
      label: t("circle.tabs.members"),
      render: (context) => <CircleMembersTab {...context} />,
    },
  ];
}

/**
 * Circles: the private, overlapping grouping of parties. A circle may nest under
 * a parent circle (one tree — overlap comes from a party holding many
 * memberships, never from multiple parents), so the form carries the parent
 * relation and the list groups by it.
 */
export function CirclesPage(): React.ReactElement {
  const t = usePartiesT();
  const tabs = React.useMemo(() => circleRecordTabs(t), [t]);
  return (
    <ResourceList resource={MODEL} placement="inline" routed recordTabs={tabs}>
      <List resource={MODEL}>
        <Column field="name" />
        <Column field="parent.name" header={t("circle.parent")} />
        <Column field="color" widget="colorDot" />
        <Column field="position" />
        <Column field="created_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="name" title />
        <Group label={t("circle.group.details")} columns={2}>
          <Field name="parent" label={t("circle.parent")} />
          <Field name="color" label={t("circle.field.color")} />
          <Field name="icon" label={t("circle.field.icon")} />
          <Field name="position" label={t("circle.field.position")} />
        </Group>
        <Field name="description" />
      </Form>
    </ResourceList>
  );
}
