import * as React from "react";
import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  List,
} from "@angee/ui";

const ADDRESS = "parties.Address";

/** One evaluated field inventory for canonical forms and domain-owned commands. */
export function addressFields({ includeParty = true, prefix = "" }: { includeParty?: boolean; prefix?: string } = {}): React.ReactNode {
  return [
    includeParty ? <Field key="party" name="party" readOnly /> : null,
    <Field key="label" name={`${prefix}label`} kind="string" label="Address label" title />,
    <Field key="street" name={`${prefix}street`} kind="string" label="Street" widget="textarea" />,
    <Field key="extended" name={`${prefix}extended`} kind="string" label="Extended address" widget="textarea" />,
    <Field key="po_box" name={`${prefix}po_box`} kind="string" label="PO box" />,
    <Field key="city" name={`${prefix}city`} kind="string" label="City" />,
    <Field key="region" name={`${prefix}region`} kind="string" label="Region" />,
    <Field key="postal_code" name={`${prefix}postal_code`} kind="string" label="Postal code" />,
    <Field key="country" name={`${prefix}country`} kind="string" label="Country" />,
    <Field key="is_primary" name={`${prefix}is_primary`} kind="boolean" label="Primary address" widget="switch" />,
  ];
}

/** Canonical create/edit address collection shared by every Party subtype; its tab supplies the empty copy. */
export function PartyAddresses({ recordId, emptyContent }: { recordId: string; emptyContent: string }): React.ReactElement {
  return (
    <DrawerResourceList
      resource={ADDRESS}
      scope="local"
      baseFilter={{ party: { exact: recordId } }}
      createDefaults={{ party: recordId }}
    >
      <List resource={ADDRESS} order={{ is_primary: "DESC" }} emptyContent={emptyContent}>
        <Column field="label" />
        <Column field="street" />
        <Column field="extended" />
        <Column field="po_box" />
        <Column field="city" />
        <Column field="region" />
        <Column field="postal_code" />
        <Column field="country" />
        <Column field="is_primary" />
      </List>
      <Form resource={ADDRESS}>
        {addressFields()}
      </Form>
    </DrawerResourceList>
  );
}
