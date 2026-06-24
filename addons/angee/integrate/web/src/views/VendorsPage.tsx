import * as React from "react";
import { Column, DataPage, Field, Form, GroupListView, List } from "@angee/base";

const MODEL = "integrate.Vendor";

const vendorList = (
  <List model={MODEL} list={GroupListView}>
    <Column field="slug" />
    <Column field="display_name" />
    <Column field="website_url" />
  </List>
);

const vendorForm = (
  <Form model={MODEL}>
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
    <DataPage model={MODEL} placement="inline" routed>
      {vendorList}
      {vendorForm}
    </DataPage>
  );
}
