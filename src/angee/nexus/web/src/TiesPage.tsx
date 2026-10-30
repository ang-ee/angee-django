import * as React from "react";
import { Column, Field, Form, Group, List, ResourceList } from "@angee/ui";

import { useNexusT } from "./i18n";

const MODEL = "nexus.Tie";

/** Derived party-pair analytics, browsed through the shared resource surface. */
export function TiesPage(): React.ReactElement {
  const t = useNexusT();
  return (
    <ResourceList resource={MODEL} placement="inline" routed hideCreate>
      <List resource={MODEL}>
        <Column field="party_a.display_name" header={t("ties.partyA")} />
        <Column field="party_b.display_name" header={t("ties.partyB")} />
        <Column field="a_to_b_count" header={t("ties.aToB")} />
        <Column field="b_to_a_count" header={t("ties.bToA")} />
        <Column field="message_count" header={t("ties.messages")} />
        <Column field="gravity" header={t("ties.gravity")} />
        <Column field="last_interaction_at" header={t("ties.lastContact")} />
        <Column field="is_fading" header={t("ties.fading")} widget="booleanBadge" />
      </List>
      <Form resource={MODEL}>
        <Group label={t("ties.group.pair")} columns={2}>
          <Field name="party_a" label={t("ties.partyA")} readOnly />
          <Field name="party_b" label={t("ties.partyB")} readOnly />
          <Field name="a_to_b_count" label={t("ties.aToB")} readOnly />
          <Field name="b_to_a_count" label={t("ties.bToA")} readOnly />
        </Group>
        <Group label={t("ties.group.analytics")} columns={2}>
          <Field name="message_count" readOnly />
          <Field name="thread_count" readOnly />
          <Field name="platforms" widget="json" readOnly />
          <Field name="gravity" readOnly />
          <Field name="first_interaction_at" readOnly />
          <Field name="last_interaction_at" readOnly />
          <Field name="is_fading" readOnly />
        </Group>
      </Form>
    </ResourceList>
  );
}
