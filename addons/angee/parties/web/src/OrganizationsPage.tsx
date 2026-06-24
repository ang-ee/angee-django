import * as React from "react";
import { Column, DataPage, Field, Form, Group, GroupListView, List } from "@angee/base";

const MODEL = "parties.Organization";

const organizationsList = (
  <List model={MODEL} list={GroupListView}>
    <Column field="display_name" />
    <Column field="domain" />
    <Column field="created_at" />
  </List>
);

const organizationsForm = (
  <Form model={MODEL}>
    <Field name="display_name" title />
    <Group label="Details" columns={2}>
      <Field name="legal_name" label="Legal name" />
      <Field name="domain" label="Domain" />
    </Group>
    <Field name="notes" />
  </Form>
);

/** Organizations (the organisation-kind contacts): full create/edit/list/detail. */
export function OrganizationsPage(): React.ReactElement {
  return (
    <DataPage model={MODEL} placement="inline" routed>
      {organizationsList}
      {organizationsForm}
    </DataPage>
  );
}
