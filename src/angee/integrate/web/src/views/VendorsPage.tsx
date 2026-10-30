import * as React from "react";
import { Column, ResourceList, Field, Form, List } from "@angee/ui";

const MODEL = "integrate.Vendor";

const vendorList = (
  <List resource={MODEL}>
    <Column field="slug" />
    <Column field="display_name" />
    <Column field="website_url" />
  </List>
);

const vendorForm = (
  <Form resource={MODEL}>
    <Field name="display_name" title />
    <Field name="slug" widget="slug" />
    <Field name="icon" />
    <Field name="website_url" />
    <Field name="description" />
  </Form>
);

/** The third-party vendor catalogue. */
export function VendorsPage(): React.ReactElement {
  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      {vendorList}
      {vendorForm}
    </ResourceList>
  );
}
