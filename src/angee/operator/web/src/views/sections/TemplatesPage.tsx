import { Code, OverflowChipList, type ResourceToolbarGroupOption, type ListColumn } from "@angee/ui";
import { ResourceQuery } from "@angee/metadata";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import type { TemplateDescriptor } from "../../data/types";
import { daemonRows, type DaemonRow } from "../parts/daemon-rows";
import { OperatorRowsList } from "../parts/operator-rows";

type TemplateRow = DaemonRow<TemplateDescriptor & { title: string }>;
const templateRowsQuery = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, ref: { scalar: "String" }, kind: { scalar: "String" }, path: { scalar: "String" },
} });

/** Templates page: the addable template catalog, grouped by kind. */
export function TemplatesPage(): ReactNode {
  const t = useOperatorT();

  const columns = useMemo<readonly ListColumn<TemplateRow>[]>(
    () => [
      { field: "title", header: t("templates.column.name"), subline: "ref" },
      { field: "kind", header: t("templates.column.kind") },
      { field: "path", header: t("templates.column.path"), render: (template) => <Code truncate>{template.path}</Code> },
      {
        field: "inputs", header: t("templates.inputs"), sortable: false,
        // Required inputs carry the warning tone; the rest are neutral.
        render: (template) => (
          <OverflowChipList tone="neutral" items={template.inputs.map((input) => ({
            id: input.name, label: input.name, ...(input.required ? { tone: "warning" as const } : {}),
          }))} />
        ),
      },
    ],
    [t],
  );

  const groupOptions: readonly ResourceToolbarGroupOption[] = useMemo(
    () => [{ id: "kind", label: t("templates.column.kind"), group: { field: "kind" }, type: "value" }],
    [t],
  );

  return (
    <OperatorRowsList<TemplateRow>
      query={templateRowsQuery}
      sections={{ templates: true }}
      selectRows={(snapshot) => daemonRows(
        snapshot.templates.map((template) => ({ ...template, title: template.name || template.ref })),
        (template) => template.ref,
      )}
      columns={columns}
      groupOptions={groupOptions}
      emptyContent={t("templates.empty.title")}
    />
  );
}
