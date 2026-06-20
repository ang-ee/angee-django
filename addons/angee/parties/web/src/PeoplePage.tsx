import * as React from "react";
import { Column, DataPage, Field, Form, Group, List } from "@angee/base";

const MODEL = "parties.Person";

const peopleList = (
  <List model={MODEL}>
    <Column field="displayName" />
    <Column field="givenName" />
    <Column field="familyName" />
    <Column field="createdAt" />
  </List>
);

const peopleForm = (
  <Form model={MODEL}>
    <Field name="displayName" title />
    <Group label="Name" columns={2}>
      <Field name="givenName" label="Given name" />
      <Field name="familyName" label="Family name" />
      <Field name="additionalName" label="Middle name" />
      <Field name="nickname" label="Nickname" />
      <Field name="namePrefix" label="Prefix" />
      <Field name="nameSuffix" label="Suffix" />
    </Group>
    <Group label="Details" columns={2}>
      <Field name="birthday" label="Birthday" />
      <Field name="anniversary" label="Anniversary" />
    </Group>
    <Field name="notes" />
  </Form>
);

/** People (the person-kind contacts): full create/edit/list/detail. */
export function PeoplePage(): React.ReactElement {
  return (
    <DataPage model={MODEL} placement="inline" routed>
      {peopleList}
      {peopleForm}
    </DataPage>
  );
}
