import * as React from "react";
import {
  Column,
  DrawerResourceList,
  Field,
  Form,
  List,
} from "@angee/ui";

import { usePartiesT } from "./i18n";

const ADDRESS = "parties.Address";

export interface AddressFieldLabels {
  label: string;
  street: string;
  extended: string;
  poBox: string;
  city: string;
  region: string;
  postalCode: string;
  country: string;
  primary: string;
}

/** One evaluated field inventory for canonical forms and domain-owned commands. */
export function addressFields({
  includeParty = true,
  prefix = "",
  labels,
}: {
  includeParty?: boolean;
  prefix?: string;
  labels: AddressFieldLabels;
}): React.ReactNode {
  return [
    includeParty ? <Field key="party" name="party" readOnly /> : null,
    <Field key="label" name={`${prefix}label`} kind="string" label={labels.label} title />,
    <Field key="street" name={`${prefix}street`} kind="string" label={labels.street} widget="textarea" body={false} />,
    <Field key="extended" name={`${prefix}extended`} kind="string" label={labels.extended} widget="textarea" body={false} />,
    <Field key="po_box" name={`${prefix}po_box`} kind="string" label={labels.poBox} />,
    <Field key="city" name={`${prefix}city`} kind="string" label={labels.city} />,
    <Field key="region" name={`${prefix}region`} kind="string" label={labels.region} />,
    <Field key="postal_code" name={`${prefix}postal_code`} kind="string" label={labels.postalCode} />,
    <Field key="country" name={`${prefix}country`} kind="string" label={labels.country} />,
    <Field key="is_primary" name={`${prefix}is_primary`} kind="boolean" label={labels.primary} widget="switch" />,
  ];
}

/** Canonical create/edit address collection shared by every Party subtype. */
export function PartyAddresses({ recordId }: { recordId: string }): React.ReactElement {
  const t = usePartiesT();
  const labels: AddressFieldLabels = {
    label: t("address.label"),
    street: t("address.street"),
    extended: t("address.extended"),
    poBox: t("address.poBox"),
    city: t("address.city"),
    region: t("address.region"),
    postalCode: t("address.postalCode"),
    country: t("address.country"),
    primary: t("address.primary"),
  };
  return (
    <DrawerResourceList
      resource={ADDRESS}
      scope="local"
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
        {addressFields({ labels })}
      </Form>
    </DrawerResourceList>
  );
}
