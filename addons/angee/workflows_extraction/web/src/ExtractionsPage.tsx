import * as React from "react";
import { Column, Field, Form, List, ResourceList } from "@angee/ui";

const EXTRACTION_MODEL = "workflows_extraction.Extraction";

export function ExtractionsPage(): React.ReactElement {
  return <ResourceList resource={EXTRACTION_MODEL} placement="inline" routed hideCreate defaultRecordTab="evidence">
    <List resource={EXTRACTION_MODEL}>
      <Column field="revision" />
      <Column field="status" />
      <Column field="schema_id" />
      <Column field="profile" />
      <Column field="created_at" />
    </List>
    <Form resource={EXTRACTION_MODEL}>
      <Field name="revision" readOnly title />
      <Field name="status" readOnly />
      <Field name="error_code" readOnly />
      <Field name="schema_id" readOnly />
      <Field name="schema_digest" readOnly />
      <Field name="profile" readOnly />
      <Field name="model" readOnly />
      <Field name="recognition_model" readOnly />
      <Field name="created_at" readOnly />
    </Form>
  </ResourceList>;
}
