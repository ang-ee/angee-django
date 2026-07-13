import * as React from "react";
import { Column, Field, Form, Group, List, ResourceList } from "@angee/ui";
import { usePartiesT } from "./i18n";

const MODEL = "parties.Relationship";

/**
 * Relationships: typed edges from one contact's viewpoint — the counterparty is
 * the contact's *kind* ("Mother", "Mentor", "Colleague"). The vocabulary is data
 * (`parties.RelationshipKind`, XFN + kinship seeded), picked through the kind
 * relation field; the counterparty is a tracked contact or a free-text name
 * (relatives who are not directory entries); edges are time-bounded so an ended
 * one stays queryable history rather than being deleted.
 */
export function RelationshipsPage(): React.ReactElement {
  const t = usePartiesT();
  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      <List resource={MODEL}>
        <Column field="party.display_name" header={t("relationship.party")} />
        <Column field="kind.name" header={t("relationship.kind")} />
        <Column field="other_party.display_name" header={t("relationship.other")} />
        <Column field="other_name" header={t("relationship.otherName")} />
        <Column field="started_at" />
        <Column field="ended_at" />
      </List>
      <Form resource={MODEL}>
        <Group label={t("relationship.group.edge")} columns={2}>
          <Field name="party" label={t("relationship.party")} />
          <Field name="kind" label={t("relationship.kind")} />
          <Field name="other_party" label={t("relationship.other")} />
          <Field name="other_name" label={t("relationship.otherName")} />
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
