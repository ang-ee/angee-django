import * as React from "react";
import { Column, Field, Form, Group, List, ResourceList } from "@angee/ui";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Relationship";

/**
 * Relationships: the typed, directed party↔party edges ("from is *kind* of to").
 * The vocabulary is data (`parties.RelationshipKind`, XFN-seeded), picked through
 * the kind relation field; edges are time-bounded so an ended one stays
 * queryable history rather than being deleted.
 */
export function RelationshipsPage(): React.ReactElement {
  const t = usePartiesT();
  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      <List resource={MODEL}>
        <Column field="from_party.display_name" header={t("relationship.from")} />
        <Column field="kind.name" header={t("relationship.kind")} />
        <Column field="to_party.display_name" header={t("relationship.to")} />
        <Column field="started_at" />
        <Column field="ended_at" />
      </List>
      <Form resource={MODEL}>
        <Group label={t("relationship.group.edge")} columns={2}>
          <Field name="from_party" label={t("relationship.from")} />
          <Field name="to_party" label={t("relationship.to")} />
          <Field name="kind" label={t("relationship.kind")} />
        </Group>
        <Group label={t("relationship.group.period")} columns={2}>
          <Field name="started_at" label={t("relationship.field.startedAt")} />
          <Field name="ended_at" label={t("relationship.field.endedAt")} />
        </Group>
        <Field name="notes" />
      </Form>
    </ResourceList>
  );
}
