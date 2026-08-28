import * as React from "react";
import { Column, Field, Form, Group, List, ResourceList } from "@angee/ui";

import { useNexusT } from "./i18n";

const MODEL = "nexus.Cadence";

/** Viewer-owned stay-in-touch intent over readable parties. */
export function CadencesPage(): React.ReactElement {
  const t = useNexusT();
  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      <List resource={MODEL}>
        <Column field="party.display_name" header={t("cadences.party")} />
        <Column field="cadence_days" header={t("cadences.days")} />
        <Column field="touch_due_at" header={t("cadences.touchDue")} />
      </List>
      <Form resource={MODEL}>
        <Field name="party" label={t("cadences.party")} title createOnly />
        <Group label={t("cadences.group.schedule")} columns={2}>
          <Field name="cadence_days" label={t("cadences.days")} />
          <Field name="touch_due_at" label={t("cadences.touchDue")} readOnly editOnly />
        </Group>
      </Form>
    </ResourceList>
  );
}
