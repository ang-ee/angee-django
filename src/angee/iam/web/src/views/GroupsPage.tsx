import * as React from "react";
import { Column, ResourceList, Field, Form, List, type RecordTabDescriptor } from "@angee/ui";

import { useIamT } from "../i18n";
import { usePrincipalAccessRecordTab } from "../PrincipalAccess";
import { GroupMembersTab } from "./GroupAccessTabs";

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
  const accessTab = usePrincipalAccessRecordTab();
  const tabs = React.useMemo<readonly RecordTabDescriptor[]>(() => [
    { id: "members", label: t("group.members"), render: (context) => <GroupMembersTab {...context} /> },
    accessTab,
  ], [accessTab, t]);
  return (
    <ResourceList
      resource={MODEL}
      placement="inline"
      routed
      returning={["assignment_subject"]}
      recordTabs={tabs}
    >
      {groupList}
      {groupForm}
    </ResourceList>
  );
}
