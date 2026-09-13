import * as React from "react";
import { Column, ResourceList, Field, Form, List, type RecordTabDescriptor } from "@angee/ui";

import { useIamT } from "../i18n";
import { GroupBindingsTab, GroupMembersTab } from "./GroupAccessTabs";

const MODEL = "iam.Group";

const groupList = (
  <List resource={MODEL} order={{ name: "ASC" }}>
    <Column field="name" />
    <Column field="description" />
  </List>
);

const groupForm = (
  <Form resource={MODEL}>
    <Field name="name" title />
    <Field name="description" />
  </Form>
);

export function GroupsPage(): React.ReactElement {
  const t = useIamT();
  const tabs = React.useMemo<readonly RecordTabDescriptor[]>(() => [
    { id: "members", label: t("group.members"), render: (context) => <GroupMembersTab {...context} /> },
    { id: "bindings", label: t("group.bindings"), render: (context) => <GroupBindingsTab {...context} /> },
  ], [t]);
  return (
    <ResourceList resource={MODEL} placement="inline" routed recordTabs={tabs}>
      {groupList}
      {groupForm}
    </ResourceList>
  );
}
