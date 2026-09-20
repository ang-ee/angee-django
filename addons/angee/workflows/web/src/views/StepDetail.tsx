import * as React from "react";

import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import { canonicalOptionValue, Code, ErrorBanner, errorMessage, FormView, jsonObjectFromUnknown, relationValueId, TextLink, useImplConfigFields, useRouteHref, useRouteRecordId, type FormField, type RecordPanelContext, type RecordToolbarContext } from "@angee/ui";

import { WorkflowStepOperationsDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { WorkflowInputBindingEditor } from "./WorkflowInputBindingEditor";
import {
  WORKFLOW_STEP_USAGE_FIELDS,
  workflowLabel,
  workflowUsage,
} from "./workflow-step-usage";

type Operation = DocumentType<typeof WorkflowStepOperationsDocument>["workflow_step_operations"][number];

export function StepDetail(): React.ReactElement {
  const id = useRouteRecordId();
  const t = useWorkflowsT();
  const routeHref = useRouteHref();
  const operationsQuery = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operations = operationsQuery.data?.workflow_step_operations ?? [];
  const implConfig = useImplConfigFields("workflows.Step", "step_class", operations);
  const fields = React.useMemo<readonly FormField[]>(() => [
    { name: "name", label: t("steps.name"), title: true, readOnly: true },
    { name: "key", label: t("steps.key"), readOnly: true },
    { name: "workflow", label: t("steps.workflow"), readOnly: true },
    { name: "step_class", label: t("steps.type"), readOnly: true },
    { name: "join_rule", label: t("steps.joinRule"), readOnly: true },
    { name: "is_entry", label: t("steps.entry"), readOnly: true },
    { name: "config_errors", hidden: true, readOnly: true },
    { name: "input_binding", hidden: true, readOnly: true },
    ...implConfig.fields.map((field) => ({ ...field, readOnly: true })),
    { name: "config", label: t("steps.config"), widget: "json", readOnly: true, showWhen: (values) => !implConfig.hasSchema(values.step_class) },
  ], [implConfig, t]);
  const operationFor = React.useCallback((record: Readonly<Record<string, unknown>> | null): Operation | undefined => {
    const key = canonicalOptionValue(operations.map((operation) => ({ value: operation.key, label: operation.label })), record?.step_class);
    return operations.find((operation) => operation.key === key);
  }, [operations]);
  const headerExtras = React.useCallback((context: RecordToolbarContext) => {
    const operation = operationFor(context.record);
    const platformHref = operation ? routeHref.maybe("platform.implementations.record", { id: `workflows.Step.step_class:${operation.key}` }) : undefined;
    return operation ? <span className="flex flex-wrap items-center gap-2 text-sm"><Code>{operation.label}</Code>{platformHref ? <TextLink href={platformHref}>{t("steps.openType")}</TextLink> : null}</span> : null;
  }, [operationFor, routeHref, t]);
  const recordExtras = React.useCallback((context: RecordPanelContext) => {
    const record = context.form.displayRecord;
    if (!record) return null;
    const workflow = workflowUsage(record.workflow);
    const workflowId = workflow?.id ?? relationValueId(record.workflow);
    const operation = operationFor(record);
    const errors = diagnosticMessages(record.config_errors);
    return <div className="grid gap-4">
      {operationsQuery.error ? <ErrorBanner description={errorMessage(operationsQuery.error, t("steps.typesUnavailable"))} /> : null}
      {workflowId ? <p><TextLink href={routeHref("workflows.workflow", { id: workflowId })}>
        {workflow ? workflowLabel(workflow) : t("steps.openWorkflow")}
      </TextLink></p> : null}
      {errors.length ? <ErrorBanner title={t("steps.configDiagnostics")} description={errors.join(" ")} /> : null}
      <section className="grid gap-2">
        <h2 className="text-base font-semibold">{t("steps.inputBinding")}</h2>
        <WorkflowInputBindingEditor nodeKey={typeof record.key === "string" ? record.key : ""} operation={operation} value={jsonObjectFromUnknown(record.input_binding) ?? null} readOnly messages={[]} onChange={() => undefined} onCommit={() => undefined} onStructuralChange={() => undefined} onFocused={() => undefined} />
      </section>
    </div>;
  }, [operationFor, operationsQuery.error, routeHref, t]);
  return <FormView resource="workflows.Step" id={id} readOnly publishBreadcrumbLabel
    fields={fields} returning={WORKFLOW_STEP_USAGE_FIELDS}
    headerExtras={headerExtras} recordExtras={recordExtras} />;
}

function diagnosticMessages(value: unknown): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value).flatMap(([field, messages]) => Array.isArray(messages) ? messages.map((message) => `${field}: ${String(message)}`) : [`${field}: ${String(messages)}`]);
}
