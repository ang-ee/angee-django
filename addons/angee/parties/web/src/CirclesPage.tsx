import * as React from "react";
import {
  Column,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  type RecordTabDescriptor,
} from "@angee/ui";
import { CircleMembersList } from "./CircleMembershipList";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Circle";

function circleRecordTabs(t: ReturnType<typeof usePartiesT>): readonly RecordTabDescriptor[] {
  return [
    {
      id: "members",
      label: t("circle.tabs.members"),
      render: ({ recordId }) => <CircleMembersList circleId={recordId} />,
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
