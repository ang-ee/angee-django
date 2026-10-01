import * as React from "react";
import { Column, ResourceList, List } from "@angee/ui";
import { organizationForm } from "./OrganizationForm";

const MODEL = "parties.Organization";

const organizationsList = (
  <List resource={MODEL}>
    <Column field="display_name" />
    <Column field="domain" />
    <Column field="created_at" />
  </List>
);

/** Organizations (the organisation-kind contacts): full create/edit/list/detail. */
export function OrganizationsPage(): React.ReactElement {
  return (
    <ResourceList resource={MODEL} form={organizationForm} placement="inline" routed>
      {organizationsList}
    </ResourceList>
  );
}
