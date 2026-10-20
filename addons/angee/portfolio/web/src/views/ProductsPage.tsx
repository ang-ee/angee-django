import {
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  useEnumOptions,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { PRODUCT_MODEL } from "../resources";

/** Product collection and phasal record surface with projects and releases. */
export function ProductsPage(): React.ReactElement {
  const t = usePortfolioT();
  const lifecycleOptions = useEnumOptions(PRODUCT_MODEL, "lifecycle");
  return (
    <ResourceList
      resource={PRODUCT_MODEL}
      placement="inline"
      routed
    >
      <List
        resource={PRODUCT_MODEL}
        defaultGroup={{ field: "lifecycle" }}
        order={{ name: "ASC" }}
      >
        <Facet field="owner" label={t("common.owner")} />
        <Column field="name" header={t("common.name")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="owner" />
        <Column field="originated_from" />
        <Column field="updated_at" />
      </List>
      <Form resource={PRODUCT_MODEL} eyebrow={t("product.eyebrow")}>
        <Field name="name" title />
        <Field
          name="lifecycle"
          widget="statusbar" status
          options={lifecycleOptions}
        />
        <Group label={t("product.group.provenance")} columns={2}>
          <Field name="owner" />
          <Field name="originated_from" readOnly />
        </Group>
        <Field name="body" widget="markdown.editor" body />
      </Form>
    </ResourceList>
  );
}

