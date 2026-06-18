import * as React from "react";
import { Column, DataPage, Field, Form, List } from "@angee/base";

const MODEL = "integrate.Vendor";

const vendorList = (
  <List model={MODEL}>
    <Column field="slug" />
    <Column field="displayName" />
    <Column field="websiteUrl" />
  </List>
);

const vendorForm = (
  <Form model={MODEL}>
    <Field name="displayName" title />
    <Field name="slug" widget="slug" />
    <Field name="icon" />
    <Field name="websiteUrl" />
    <Field name="description" />
  </Form>
);

/** The third-party vendor catalogue. */
export function VendorsPage(): React.ReactElement {
  return (
    <DataPage model={MODEL} placement="inline" routed>
      {vendorList}
      {vendorForm}
    </DataPage>
  );
}
