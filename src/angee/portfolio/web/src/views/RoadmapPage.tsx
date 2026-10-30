import { PROJECT_MODEL } from "@angee/projects";
import {
  Column,
  DetailSection,
  List,
  Page,
  PageBody,
  PageHeader,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "../i18n";
import { INITIATIVE_PROJECT_MODEL } from "../resources";

/** Roadmap as grouped lists under the page's own header; a timeline remains a post-suite capability. */
export function RoadmapPage(): React.ReactElement {
  const t = usePortfolioT();
  return (
    <Page>
      <PageHeader title={t("roadmap.title")} description={t("roadmap.description")} />
      <PageBody className="grid gap-8">
        <DetailSection title={t("roadmap.products.title")}>
          <List<StringIdRow>
            resource={PROJECT_MODEL}
            presentation="embedded"
            defaultGroup={{ field: "product" }}
            order={{ sort_order: "ASC" }}
          >
            <Column field="title" />
            <Column field="status" widget="statusBadge" />
            <Column field="health" widget="statusBadge" />
            <Column field="target_date" />
            <Column field="priority" />
          </List>
        </DetailSection>
        <DetailSection title={t("roadmap.initiatives.title")}>
          <List<StringIdRow>
            resource={INITIATIVE_PROJECT_MODEL}
            presentation="embedded"
            defaultGroup={{ field: "initiative" }}
            order={{ sort_order: "ASC" }}
          >
            <Column field="project" />
            <Column field="project.product" />
            <Column field="project.status" widget="statusBadge" />
            <Column field="project.health" widget="statusBadge" />
            <Column field="sort_order" />
          </List>
        </DetailSection>
      </PageBody>
    </Page>
  );
}
