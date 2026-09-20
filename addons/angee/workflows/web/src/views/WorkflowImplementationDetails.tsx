import type { ReactElement, ReactNode } from "react";

import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import { Code, CodeBlock, ControlBandProvider, DetailSection, ErrorBanner, errorMessage, ListView, LoadingPanel, TextLink, useImplementationDetailContext, useRouteHref, type ListColumn, type ResourceToolbarGroupOption, type StringIdRow } from "@angee/ui";

import { WorkflowStepOperationsDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { WORKFLOW_STEP_USAGE_FIELDS, workflowLabel, type WorkflowStepUsage } from "./workflow-step-usage";

type Operation = DocumentType<typeof WorkflowStepOperationsDocument>["workflow_step_operations"][number];
interface UsageRow extends StringIdRow {
  name: string;
  key: string;
  workflow: WorkflowStepUsage;
}

export function WorkflowImplementationDetails(): ReactElement | null {
  const { field, choice } = useImplementationDetailContext();
  const t = useWorkflowsT();
  const routeHref = useRouteHref();
  const query = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operation = query.data?.workflow_step_operations.find((candidate) => candidate.key === choice.key);
  if (field !== "step_class") return null;
  if (query.isFetching && !operation) return <LoadingPanel message={t("stepTypes.loading")} />;
  if (query.error && !operation) return <ErrorBanner description={errorMessage(query.error, t("steps.typesUnavailable"))} />;
  if (!operation) return null;
  const columns: readonly ListColumn<UsageRow>[] = [
    { field: "name", header: t("steps.name"), render: (row) => <TextLink href={routeHref("workflows.step", { id: row.id })}>{row.name}</TextLink> },
    { field: "key", header: t("steps.key"), render: (row) => <Code>{row.key}</Code> },
    { field: "workflow", header: t("steps.workflow"), render: (row) => <TextLink href={routeHref("workflows.workflow", { id: row.workflow.id })}>{workflowLabel(row.workflow)}</TextLink> },
  ];
  const groups: readonly ResourceToolbarGroupOption[] = [{ id: "workflow", label: t("steps.workflow"), group: { field: "workflow" }, type: "value" }];
  return <div className="grid gap-6">
    {query.error ? <ErrorBanner description={errorMessage(query.error, t("steps.typesUnavailable"))} /> : null}
    <DetailSection title={t("stepTypes.contracts")} rows={contractRows(operation, t)} />
    <DetailSection title={t("stepTypes.behavior")} rows={[
      [t("stepTypes.effect"), operation.effect_description || operation.effect],
      [t("stepTypes.idempotent"), operation.idempotent == null ? t("stepTypes.unspecified") : String(operation.idempotent)],
      [t("stepTypes.subject"), operation.subject_declaration || t("stepTypes.none")],
      [t("stepTypes.outcomes"), operation.outcomes.length ? operation.outcomes.map((outcome) => outcome.label).join(", ") : t("stepTypes.none")],
    ]} />
    <section className="grid gap-3">
      <h2 className="text-base font-semibold">{t("stepTypes.usedBy")}</h2>
      <ControlBandProvider host={undefined}>
        <ListView<UsageRow> resource="workflows.Step" presentation="embedded" selectable={false} columns={columns} fields={WORKFLOW_STEP_USAGE_FIELDS} baseFilter={{ step_class: { exact: choice.key } }} groupOptions={groups} defaultGroup={{ field: "workflow" }} rowHref={(row) => routeHref("workflows.step", { id: row.id })} emptyContent={t("stepTypes.unused")} />
      </ControlBandProvider>
    </section>
  </div>;
}

function contractRows(operation: Operation, t: ReturnType<typeof useWorkflowsT>): readonly (readonly [string, ReactNode])[] {
  return [
    [t("stepTypes.input"), <ContractSummary contract={operation.input_contract} schema={operation.input_schema} t={t} />],
    [t("stepTypes.output"), <ContractSummary contract={operation.output_contract} schema={operation.output_schema} t={t} />],
  ];
}

function ContractSummary({ contract, schema, t }: { contract: Operation["input_contract"]; schema: unknown; t: ReturnType<typeof useWorkflowsT> }): ReactElement {
  const edgeLabels = new Map(contract.edges.map((edge) => [edge.child_node_id, edge.key || t("stepTypes.item")]));
  return <div className="grid gap-2">
    {contract.nodes.map((node) => <div key={node.id} className="grid gap-0.5 border-l border-border-subtle pl-3">
      <span className="flex flex-wrap items-center gap-2"><strong>{edgeLabels.get(node.id) || node.title || node.kind}</strong>{node.title && edgeLabels.has(node.id) ? <span>{node.title}</span> : null}{node.json_type ? <Code>{node.json_type}</Code> : null}{node.nullable ? <span className="text-xs text-fg-muted">{t("stepTypes.nullable")}</span> : null}</span>
      {node.description ? <span className="text-xs text-fg-muted">{node.description}</span> : null}
    </div>)}
    {schema ? <details><summary className="cursor-pointer text-xs text-fg-muted">{t("stepTypes.schema")}</summary><CodeBlock wrap>{JSON.stringify(schema, null, 2)}</CodeBlock></details> : null}
  </div>;
}
