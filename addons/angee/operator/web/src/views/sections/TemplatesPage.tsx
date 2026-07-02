import { Badge, Code, type ResourceToolbarGroupOption, type ListColumn } from "@angee/ui";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import type { TemplateDescriptor } from "../../data/types";
import { daemonRows, type DaemonRow } from "../parts/daemon-rows";
import { OperatorRowsList } from "../parts/operator-rows";

type TemplateRow = DaemonRow<TemplateDescriptor>;

const MAX_INPUT_CHIPS = 6;

/** Templates page: the addable template catalog, grouped by kind. */
export function TemplatesPage(): ReactNode {
  const t = useOperatorT();

  const columns = useMemo<readonly ListColumn<TemplateRow>[]>(
    () => [
      {
        field: "name",
        header: t("templates.column.name"),
        render: (template) => (
          <span className="flex min-w-0 flex-col">
            <span className="truncate font-medium text-fg">{template.name ?? template.ref}</span>
            <span className="truncate font-mono text-2xs text-fg-muted">{template.ref}</span>
          </span>
        ),
      },
      {
        field: "kind",
        header: t("templates.column.kind"),
        render: (template) => (
          <Badge density="compact" shape="pill" tone="neutral">{template.kind}</Badge>
        ),
      },
      {
        field: "path",
        header: t("templates.column.path"),
        render: (template) => <Code truncate>{template.path}</Code>,
      },
      {
        field: "inputs",
        header: t("templates.inputs"),
        sortable: false,
        render: (template) => <TemplateInputs template={template} />,
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
      sections={{ templates: true }}
      selectRows={(snapshot) =>
        daemonRows(snapshot.templates, (template) => template.ref)
      }
      columns={columns}
      groupOptions={groupOptions}
      emptyContent={t("templates.empty.title")}
    />
  );
}

/** A template's input schema as a compact chip wrap; required inputs stand out. */
function TemplateInputs({ template }: { template: TemplateDescriptor }): ReactNode {
  if (template.inputs.length === 0) {
    return <span className="text-fg-muted">—</span>;
  }
  const shown = template.inputs.slice(0, MAX_INPUT_CHIPS);
  const overflow = template.inputs.length - shown.length;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {shown.map((input) => (
        <Badge
          key={input.name}
          density="compact"
          shape="pill"
          tone={input.required ? "warning" : "neutral"}
        >
          {input.name}
        </Badge>
      ))}
      {overflow > 0 ? (
        <Badge density="compact" shape="pill" tone="neutral">{`+${overflow}`}</Badge>
      ) : null}
    </span>
  );
}
