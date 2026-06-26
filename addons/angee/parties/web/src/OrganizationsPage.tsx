import * as React from "react";
import { Column, ResourceList, Field, Form, Group, List } from "@angee/ui";

const MODEL = "parties.Organization";

const organizationsList = (
  <List resource={MODEL}>
    <Column field="display_name" />
    <Column field="domain" />
    <Column field="created_at" />
  </List>
);

const organizationsForm = (
  <Form resource={MODEL}>
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
    <ResourceList resource={MODEL} placement="inline" routed>
      {organizationsList}
      {organizationsForm}
    </ResourceList>
  );
}
