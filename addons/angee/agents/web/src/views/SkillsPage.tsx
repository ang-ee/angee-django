import * as React from "react";
import { Column, DataPage, Facet, Field, Form, GroupListView, List } from "@angee/base";

const MODEL = "agents.Skill";

// Skills are discovered from a source, not authored here: a read-only list with no
// create. The Form is read-only (the model has no create/update mutation) but must be
// present — `DataPage` resolves its form fields eagerly on mount.
export function SkillsPage(): React.ReactElement {
  return (
    <DataPage model={MODEL} placement="inline" hideCreate>
      <List model={MODEL} list={GroupListView}>
        <Facet field="source" label="Source" labelField="path" />
        <Column field="name" />
        <Column field="description" />
        <Column field="path" />
        <Column field="updatedAt" />
      </List>
      <Form model={MODEL}>
        <Field name="name" title readOnly />
        <Field name="description" readOnly />
        <Field name="source" readOnly />
        <Field name="path" readOnly />
        <Field name="metadata" widget="json" readOnly />
      </Form>
    </DataPage>
  );
}
