import * as React from "react";
import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  List,
} from "@angee/ui";

const ADDRESS = "parties.Address";

/** One field inventory for canonical address forms and domain-owned create commands. */
export function AddressFields({ includeParty = true, prefix = "" }: { includeParty?: boolean; prefix?: string }): React.ReactElement {
  return <>
    {includeParty ? <Field name="party" readOnly /> : null}
    <Field name={`${prefix}label`} kind="string" label="Address label" title />
    <Field name={`${prefix}street`} kind="string" label="Street" widget="textarea" />
    <Field name={`${prefix}extended`} kind="string" label="Extended address" widget="textarea" />
    <Field name={`${prefix}po_box`} kind="string" label="PO box" />
    <Field name={`${prefix}city`} kind="string" label="City" />
    <Field name={`${prefix}region`} kind="string" label="Region" />
    <Field name={`${prefix}postal_code`} kind="string" label="Postal code" />
    <Field name={`${prefix}country`} kind="string" label="Country" />
    <Field name={`${prefix}is_primary`} kind="boolean" label="Primary address" widget="switch" />
  </>;
}

/** Canonical create/edit address collection shared by every Party subtype. */
export function PartyAddresses({ recordId }: { recordId: string }): React.ReactElement {
  return (
    <DrawerResourceList
      resource={ADDRESS}
      baseFilter={{ party: { exact: recordId } }}
      createDefaults={{ party: recordId }}
    >
      <List resource={ADDRESS} order={{ is_primary: "DESC" }}>
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
        <AddressFields />
      </Form>
    </DrawerResourceList>
  );
}
