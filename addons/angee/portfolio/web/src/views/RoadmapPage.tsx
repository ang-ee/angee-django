import { PROJECT_MODEL } from "@angee/projects";
import {
  Column,
  List,
  SettingsSection,
  SettingsShell,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { INITIATIVE_PROJECT_MODEL } from "../resources";

/** Roadmap as grouped lists; a timeline remains a post-suite capability. */
export function RoadmapPage(): React.ReactElement {
  const t = usePortfolioT();
  return (
    <SettingsShell maxWidth="1200" gap="8">
      <SettingsSection
        title={t("roadmap.products.title")}
        description={t("roadmap.products.description")}
      >
        <List<StringIdRow>
          resource={PROJECT_MODEL}
          defaultGroup={{ field: "product" }}
          order={{ sort_order: "ASC" }}
        >
          <Column field="title" />
          <Column field="status" widget="statusBadge" />
          <Column field="health" widget="statusBadge" />
          <Column field="target_date" />
          <Column field="priority" />
        </List>
      </SettingsSection>
      <SettingsSection
        title={t("roadmap.initiatives.title")}
        description={t("roadmap.initiatives.description")}
      >
        <List<StringIdRow>
          resource={INITIATIVE_PROJECT_MODEL}
          defaultGroup={{ field: "initiative" }}
          order={{ sort_order: "ASC" }}
        >
          <Column field="project" />
          <Column field="project.product" />
          <Column field="project.status" widget="statusBadge" />
          <Column field="project.health" widget="statusBadge" />
          <Column field="sort_order" />
        </List>
      </SettingsSection>
    </SettingsShell>
  );
}
