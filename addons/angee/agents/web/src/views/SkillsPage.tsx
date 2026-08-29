import * as React from "react";
import { Column, ResourceList, Facet, List } from "@angee/ui";

import { useAgentsT } from "../i18n";

const MODEL = "agents.Skill";

// Skills are discovered from a source, not authored here: this is a read-only
// collection surface with no create/edit form.
export function SkillsPage(): React.ReactElement {
  const t = useAgentsT();
  return (
    <ResourceList resource={MODEL} placement="inline" hideCreate>
      <List resource={MODEL}>
        <Facet field="source" label={t("facet.source")} labelField="path" />
        <Column field="name" />
        <Column field="description" />
        <Column field="path" />
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
