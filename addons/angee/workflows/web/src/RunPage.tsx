import { useState } from "react";
import { useAuthoredQuery, type ActionOutcome } from "@angee/refine";
import {
  Button, CodeBlock, Column, EmptyState, ErrorBanner, formatDateTime, JsonValueView, LazyBoundary,
  List, LoadingPanel, MetaGrid, MetaSection, Page, PageBody, RecordChrome, RecordHeader, RecordReference,
  ResourceList, TextLink, useEnumValueLabel, useRouteHref, useRouteParam, type StringIdRow,
} from "@angee/ui";
import { RunActionDialog, type RunActionTarget } from "./RunAction";
import { RUN_MODEL, RUN_MODELS, STEP_RUN_MODEL, STEP_EVIDENCE_MODELS, RunDocument, StepRunDocument, type Run, type StepRun } from "./documents.console";
import { useWorkflowsT } from "./i18n";

export function RunPage() {
  const id = useRouteParam("id");
  const t = useWorkflowsT();
  const query = useAuthoredQuery(RunDocument, { id: id ?? "" }, {
    enabled: Boolean(id), models: RUN_MODELS,
    records: id ? [{ model: RUN_MODEL, id }] : [], relatedModels: [STEP_RUN_MODEL, ...STEP_EVIDENCE_MODELS],
  });
  const run = query.data?.workflowrun_by_pk;
  if (run) return <RunDetail key={run.id} run={run} />;
  return query.isLoading ? <LoadingPanel /> : query.error
    ? <ErrorBanner description={t("run.error")} actions={<Button onClick={() => void query.refetch()}>{t("run.reload")}</Button>} />
    : <EmptyState title={t("run.notFound")} />;
}

export function RunDetail({ run }: { run: Run }) {
  const t = useWorkflowsT();
  const label = useEnumValueLabel(RUN_MODEL);
  const href = useRouteHref();
  const [action, setAction] = useState<RunActionTarget | null>(null);
  const [reprocessed, setReprocessed] = useState<ActionOutcome | null>(null);
  const workflow = run.version?.workflow;
  return <Page>
    <RecordHeader title={t("run.title", { id: run.id })} status={{ label: label("status", run.status) }} actions={<>
      <RecordChrome value={{ resource: RUN_MODEL, canonicalResource: RUN_MODEL, dataProviderName: "console", recordId: run.id, record: run, formReadOnly: true }} />
      {run.can_cancel ? <Button size="sm" variant="secondary" onClick={() => setAction({ action: "cancel_workflow_run", id: run.id })}>{t("action.cancel_workflow_run")}</Button> : null}
      {run.can_reprocess ? <Button size="sm" variant="secondary" onClick={() => setAction({ action: "reprocess_workflow_run", id: run.id })}>{t("action.reprocess_workflow_run")}</Button> : null}
    </>} />
    <PageBody className="space-y-6">
      {reprocessed ? <p role="status">{reprocessed.message}{" "}{reprocessed.id ? <TextLink href={href("workflows.runs.record", { id: reprocessed.id })}>{t("action.newRun")}</TextLink> : null}</p> : null}
      <MetaGrid rows={[
        [t("run.workflow"), workflow ? <TextLink href={href("workflows.catalogue.record", { id: workflow.id })}>{workflow.name}</TextLink> : null],
        [t("run.version"), run.version?.number],
        [t("run.subject"), run.subject_id ? <RecordReference model={run.subject_model} id={run.subject_id} /> : null],
        [t("run.origin"), label("origin", run.origin)], [t("run.runAs"), run.run_as?.display_name],
        [t("run.started"), formatDateTime(run.created_at)], [t("run.finished"), formatDateTime(run.finished_at)],
        [t("run.outcome"), run.outcome],
        [t("run.reprocessOf"), run.reprocess_of ? <TextLink href={href("workflows.runs.record", { id: run.reprocess_of.id })}>{run.reprocess_of.id}</TextLink> : null],
      ]} />
      {run.error ? <MetaSection headingLevel={2} title={t("run.retainedError")}><CodeBlock wrap>{run.error}</CodeBlock></MetaSection> : null}
      <RunValues input={run.input} output={run.output} headingLevel={2} />
      <MetaSection headingLevel={2} title={t("run.steps")}>
        <ResourceList<StringIdRow> resource={STEP_RUN_MODEL} hideCreate presentation="embedded"
          baseFilter={{ run: { exact: run.id } }} order={{ rank: "ASC", map_index: "ASC" }} pageSize={10}>
          <List<StringIdRow> renderItem={(row) => <StepRunRecord id={row.id} onAction={setAction} />} emptyContent={t("run.noSteps")}>
            <Column field="node_key" />
          </List>
        </ResourceList>
      </MetaSection>
    </PageBody>
    {action ? <RunActionDialog {...action} onClose={() => setAction(null)} onReprocessed={setReprocessed} /> : null}
  </Page>;
}

function StepRunRecord({ id, onAction }: { id: string; onAction: (target: RunActionTarget) => void }) {
  const t = useWorkflowsT();
  const query = useAuthoredQuery(StepRunDocument, { id }, {
    models: [STEP_RUN_MODEL, ...STEP_EVIDENCE_MODELS], records: [{ model: STEP_RUN_MODEL, id }], relatedModels: STEP_EVIDENCE_MODELS,
  });
  const step = query.data?.steprun_by_pk;
  if (step) return <StepRunDetail step={step} onAction={onAction} />;
  return query.isLoading ? <LoadingPanel /> : query.error
    ? <ErrorBanner description={t("step.error")} actions={<Button onClick={() => void query.refetch()}>{t("run.reload")}</Button>} />
    : <EmptyState title={t("step.notFound")} />;
}

function StepRunDetail({ step, onAction }: { step: StepRun; onAction: (target: RunActionTarget) => void }) {
  const t = useWorkflowsT();
  const label = useEnumValueLabel(STEP_RUN_MODEL);
  const result = useEnumValueLabel("workflows.StepAttempt");
  const retry = step.requires_duplicate_acknowledgement ? "retry_step_accepting_duplicate" : "retry_step";
  return <MetaSection headingLevel={3} title={`${step.node_key}${step.is_mapped ? ` [${step.map_index}]` : ""}`}>
    <MetaGrid rows={[[t("run.status"), label("status", step.status)], [t("run.outcome"), step.outcome],
      [t("step.attempts"), step.attempt], [t("step.waitKind"), label("waiting_kind", step.waiting_kind)], [t("step.waitReason"), step.wait_reason]]} />
    {step.can_retry ? <Button size="sm" variant="secondary" onClick={() => onAction({ action: retry, id: step.id })}>{t(`action.${retry}`)}</Button> : null}
    <RunValues input={step.input} output={step.output} headingLevel={4} />
    {[...step.attempts].sort((a, b) => a.number - b.number).map((attempt) => <MetaSection headingLevel={4} key={attempt.id} title={t("step.attempt", { number: attempt.number })}>
      <MetaGrid rows={[[t("step.result"), result("result", attempt.result)], [t("run.started"), formatDateTime(attempt.started_at)], [t("run.finished"), formatDateTime(attempt.finished_at)]]} />
      {attempt.error ? <CodeBlock wrap>{attempt.error}</CodeBlock> : null}
      {attempt.stacktrace ? <details><summary>{t("step.stacktrace")}</summary><CodeBlock wrap>{attempt.stacktrace}</CodeBlock></details> : null}
    </MetaSection>)}
    {step.artifacts.length ? <MetaSection headingLevel={4} title={t("step.artifacts")}><ul>{step.artifacts.map((artifact) => <li key={artifact.id}>
      <RecordReference model={artifact.model_label} id={artifact.record_id} label={artifact.label || undefined} />
    </li>)}</ul></MetaSection> : null}
  </MetaSection>;
}

function RunValues({ input, output, headingLevel }: Pick<Run, "input" | "output"> & { headingLevel: 2 | 4 }) {
  const t = useWorkflowsT();
  return <LazyBoundary pending={null}>
    <MetaSection headingLevel={headingLevel} title={t("run.input")}><JsonValueView value={input} /></MetaSection>
    <MetaSection headingLevel={headingLevel} title={t("run.output")}><JsonValueView value={output} /></MetaSection>
  </LazyBoundary>;
}
