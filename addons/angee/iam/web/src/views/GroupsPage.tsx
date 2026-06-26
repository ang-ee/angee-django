import * as React from "react";
import { Column, ResourceList, Field, Form, List } from "@angee/ui";

const MODEL = "iam.Group";

const groupList = (
  <List resource={MODEL} order={{ name: "ASC" }}>
    <Column field="name" />
  </List>
);

const groupForm = (
  <Form resource={MODEL}>
    <Field name="name" title readOnly />
  </Form>
);

export function GroupsPage(): React.ReactElement {
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate>
      {groupList}
      {groupForm}
    </ResourceList>
  );
}
