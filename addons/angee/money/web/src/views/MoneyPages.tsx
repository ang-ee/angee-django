import * as React from "react";
import {
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ResourceList,
} from "@angee/ui";

const CURRENCY_MODEL = "money.Currency";
const RATE_MODEL = "money.CurrencyRate";

export function CurrenciesPage(): React.ReactElement {
  return (
    <ResourceList resource={CURRENCY_MODEL} placement="inline" routed>
      <List resource={CURRENCY_MODEL} defaultGroup={{ field: "is_archived" }}>
        <Facet field="is_archived" label="Archived" />
        <Column field="code" />
        <Column field="name" />
        <Column field="symbol" />
        <Column field="decimal_places" header="Decimal places" />
        <Column field="updated_at" />
      </List>
      <Form resource={CURRENCY_MODEL}>
        <Field name="code" title />
        <Group label="Details" columns={2}>
          <Field name="name" />
          <Field name="symbol" />
          <Field name="decimal_places" label="Decimal places" />
        </Group>
        <Field name="is_archived" />
      </Form>
    </ResourceList>
  );
}

export function CurrencyRatesPage(): React.ReactElement {
  return (
    <ResourceList resource={RATE_MODEL} placement="inline" routed>
      <List resource={RATE_MODEL}>
        <Facet field="currency" label="Currency" labelField="code" />
        <Column field="currency.code" header="Currency" />
        <Column field="date" />
        <Column field="rate" />
        <Column field="updated_at" />
      </List>
      <Form resource={RATE_MODEL}>
        <Field name="currency" title />
        <Group label="Rate" columns={2}>
          <Field name="date" />
          <Field name="rate" />
        </Group>
      </Form>
    </ResourceList>
  );
}
