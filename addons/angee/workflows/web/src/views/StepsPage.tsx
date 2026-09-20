import type { ReactElement } from "react";

import { Code, ListView, TextLink, useRouteHref, type ListColumn, type ResourceToolbarGroupOption, type StringIdRow } from "@angee/ui";

import { useWorkflowsT } from "../i18n";
import { WORKFLOW_STEP_USAGE_FIELDS, workflowLabel, type WorkflowStepUsage } from "./workflow-step-usage";

interface StepRow extends StringIdRow {
  key: string;
  name: string;
  workflow: WorkflowStepUsage;
  step_class: string;
  join_rule: string;
  is_entry: boolean;
}

export function StepsPage(): ReactElement {
  const t = useWorkflowsT();
  const routeHref = useRouteHref();
  const columns: readonly ListColumn<StepRow>[] = [
    { field: "name", header: t("steps.name"), render: (row) => <TextLink href={routeHref("workflows.step", { id: row.id })}>{row.name}</TextLink> },
    { field: "key", header: t("steps.key"), render: (row) => <Code>{row.key}</Code> },
    { field: "workflow", header: t("steps.workflow"), render: (row) => <TextLink href={routeHref("workflows.workflow", { id: row.workflow.id })}>{workflowLabel(row.workflow)}</TextLink> },
    { field: "step_class", header: t("steps.type"), render: (row) => <Code>{row.step_class}</Code> },
    { field: "join_rule", header: t("steps.joinRule") },
    { field: "is_entry", header: t("steps.entry") },
  ];
  const groups: readonly ResourceToolbarGroupOption[] = [
    { id: "workflow", label: t("steps.workflow"), group: { field: "workflow" }, type: "value" },
    { id: "step_class", label: t("steps.type"), group: { field: "step_class" }, type: "value" },
  ];
  return <ListView<StepRow> resource="workflows.Step" columns={columns} fields={WORKFLOW_STEP_USAGE_FIELDS} groupOptions={groups} defaultGroup={{ field: "workflow" }} rowHref={(row) => routeHref("workflows.step", { id: row.id })} emptyContent={t("steps.empty")} />;
}
