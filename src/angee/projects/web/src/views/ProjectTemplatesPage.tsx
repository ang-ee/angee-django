import { Column, List, useResourceRecordHref, type StringIdRow } from "@angee/ui";
import * as React from "react";

import { useProjectsT } from "../i18n";
import { PROJECT_TEMPLATES } from "../project-declarations";
import { PROJECT_MODEL } from "../resources";

/** The project templates ordinary lists leave out; each opens on the canonical project route. */
export function ProjectTemplatesPage(): React.ReactElement {
  const t = useProjectsT();
  const recordHref = useResourceRecordHref(PROJECT_MODEL);
  const rowHref = React.useMemo(
    () => (recordHref ? (row: StringIdRow) => recordHref(row.id, row) ?? "" : undefined),
    [recordHref],
  );
  return (
    <List<StringIdRow>
      resource={PROJECT_MODEL}
      baseFilter={PROJECT_TEMPLATES}
      order={{ title: "ASC" }}
      rowHref={rowHref}
      emptyContent={t("project.templates.empty")}
    >
      <Column field="title" />
      <Column field="lead" />
      <Column field="updated_at" />
      <Column field="tags" hiddenByDefault />
    </List>
  );
}
