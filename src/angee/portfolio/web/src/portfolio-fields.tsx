import { Field, Group } from "@angee/ui";
import * as React from "react";

import { usePortfolioT } from "./i18n";

function ProjectPortfolioLabel(): React.ReactElement {
  const t = usePortfolioT();
  return <>{t("product.group.identity")}</>;
}

function ReleaseLabel(): React.ReactElement {
  const t = usePortfolioT();
  return <>{t("release.fixedIn")}</>;
}

function ReleaseDescription(): React.ReactElement {
  const t = usePortfolioT();
  return <>{t("release.fixedIn.description")}</>;
}

/** Same-row portfolio fields contributed into the projects-owned Project form. */
export const projectPortfolioFormSection = (
  <Group label={<ProjectPortfolioLabel />} columns={2}>
    <Field name="product" />
    <Field name="priority" />
    <Field name="sort_order" />
    <Field name="health" widget="statusBadge" readOnly />
    <Field name="health_updated_at" readOnly />
  </Group>
);

/** Release attribution contributed into the projects-owned Task form. */
export const taskReleaseFormSection = (
  <Group label={<ReleaseLabel />} columns={2}>
    <Field
      name="release"
      label={<ReleaseLabel />}
      description={<ReleaseDescription />}
    />
  </Group>
);
