import * as React from "react";
import { extractActionOutcome, runActionResult, useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Badge, Button, Collapsible, Column, EmptyState, ErrorBanner, errorMessage, Field, Form, Group, List,
  LoadingPanel, ResourceList, REFINE_CREATE_ID, SegmentedControl, SlotOutlet, registerForm,
  TextLink, useImplConfigFields, useFormViewValues,
  useRouteHref, useSlot, useToast, type RecordToolbarContext, type RegisteredFormProps,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import {
  DisableWorkflowTriggerDocument, EnableWorkflowTriggerDocument, WorkflowLaunchDocument,
  WorkflowSchedulePreviewDocument, WorkflowTriggerAuthoringDocument,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { JsonBlock } from "./JsonBlock";
import { WorkflowEventConditionEditor } from "./WorkflowEventConditionEditor";
import { WORKFLOW_TRIGGER_FORM_FIELDS_SLOT } from "../slots";

const WORKFLOW_MODEL = "workflows.Workflow";
const TRIGGER_MODEL = "workflows.Trigger";
export const TriggerWorkflowContext = React.createContext<{
  currentVersion: number | null;
  close: () => void;
}>({ currentVersion: null, close: () => undefined });

interface TriggerRecord {
  kind?: unknown;
  enabled?: unknown;
  config?: unknown;
  summary?: unknown;
  activation_blocker?: unknown;
  last_fire_at?: unknown;
  next_fire_at?: unknown;
}

export function WorkflowTriggersPanel({ workflowId }: { workflowId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const query = useAuthoredQuery(WorkflowLaunchDocument, { id: workflowId }, { models: [WORKFLOW_MODEL] });
  if (query.isFetching && !query.data) return <LoadingPanel message={t("triggers.loading")} />;
  if (query.error && !query.data) return <ErrorBanner description={errorMessage(query.error, t("triggers.conditionError"))} />;
  const workflow = query.data?.workflows_by_pk;
  if (!workflow) return <EmptyState title={t("triggers.unavailable")} />;
  return (
    <WorkflowTriggerCollection
      key={workflow.lineage_id}
      workflowId={workflow.lineage_id}
      readOnly={workflow.id !== workflow.lineage_id}
      currentVersion={workflow.current_published_version ?? null}
    />
  );
}

function WorkflowTriggerCollection({
  workflowId,
  readOnly,
  currentVersion,
}: {
  workflowId: string;
  readOnly: boolean;
  currentVersion: number | null;
}): React.ReactElement {
  const t = useWorkflowsT();
  const routeHref = useRouteHref();
  const navigate = useNavigate();
  const [recordId, setRecordId] = React.useState<string | undefined>();
  return (
    <TriggerWorkflowContext.Provider value={{ currentVersion, close: () => setRecordId(undefined) }}>
      {readOnly ? (
        <p>
          {t("triggers.historicalReadOnly")} {" "}
          <TextLink href={routeHref("workflows.workflow", { id: workflowId })} onNavigate={(href) => { void navigate({ to: href }); }}>
            {t("triggers.openCurrent")}
          </TextLink>
        </p>
      ) : null}
      <ResourceList
      resource={TRIGGER_MODEL}
      returning={["enabled", "summary", "activation_blocker", "last_fire_at", "next_fire_at"]}
      scope="local"
      placement="inline"
      baseFilter={{ workflow: { exact: workflowId } }}
      createDefaults={{ workflow: workflowId, enabled: false, config: {} }}
      recordId={recordId}
      onSelect={(id) => setRecordId(id ?? REFINE_CREATE_ID)}
      onClose={() => setRecordId(undefined)}
      hideCreate={readOnly}
      form={readOnly ? workflowTriggerReadOnlyForm : workflowTriggerForm}
    >
      <List resource={TRIGGER_MODEL}>
        <Column field="kind" />
        <Column field="enabled" />
        <Column field="summary" header={t("triggers.rule")} />
        <Column field="last_fire_at" />
        <Column field="next_fire_at" />
      </List>
      </ResourceList>
    </TriggerWorkflowContext.Provider>
  );
}

function WorkflowTriggerForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = useWorkflowsT();
  const authoring = useAuthoredQuery(WorkflowTriggerAuthoringDocument, {});
  const declarations = authoring.data?.workflow_trigger_declarations ?? [];
  const choices = React.useMemo(() => declarations.flatMap((declaration) => {
    const choice = {
      key: declaration.kind,
      label: declaration.label,
      category: "",
      defaults: {},
      config_schema: declaration.config_schema,
    };
    return [choice, { ...choice, key: declaration.kind.toUpperCase() }];
  }), [declarations]);
  const config = useImplConfigFields(TRIGGER_MODEL, "kind", choices);
  const extensionFields = useSlot(WORKFLOW_TRIGGER_FORM_FIELDS_SLOT);
  const publisherOptions = React.useMemo(
    () => (authoring.data?.workflow_trigger_publishers ?? []).map(({ model, label }) => ({
      value: model,
      label,
    })),
    [authoring.data?.workflow_trigger_publishers],
  );
  const configFields = React.useMemo(() => config.fields.map((field) => {
    const presentation = field.name === "config.model"
      ? { widget: "select", options: publisherOptions }
      : field.name === "config.admission_policy"
        ? {
            widget: "select",
            options: field.options?.map((option) => ({
              ...option,
              label: option.value === "each_change"
                ? t("triggers.eachChange")
                : t("triggers.oncePerSubject"),
            })),
          }
        : {};
    const cadenceName = field.name === "config.cron"
      ? "cron"
      : field.name === "config.interval_seconds" ? "interval" : null;
    const showWhen = (values: Parameters<NonNullable<typeof field.showWhen>>[0]) => (
      (field.showWhen?.(values) ?? true)
      && (cadenceName === null || scheduleMode(values.config) === cadenceName)
    );
    return {
      ...field,
      ...presentation,
      showWhen,
      resolve: field.resolve
        ? (values: Parameters<NonNullable<typeof field.resolve>>[0]) => ({
            ...field.resolve!(values),
            ...presentation,
            showWhen,
          })
        : undefined,
    };
  }), [config.fields, publisherOptions, t]);
  const advancedFields = configFields.filter((field) => (
    field.name === "config.cooldown_seconds" || field.name === "config.hourly_cap"
  ));
  const ruleFields = configFields.filter((field) => (
    !advancedFields.includes(field) && field.name !== "config.condition"
  ));
  const kindOptions = React.useMemo(
    () => declarations.map(({ kind, label }) => ({ value: kind, label })),
    [declarations],
  );
  if (authoring.isFetching && !authoring.data) return <LoadingPanel message={t("triggers.loadingDeclaration")} />;
  if (authoring.error && !authoring.data) return <ErrorBanner description={t("triggers.declarationError")} />;
  return (
    <Form
      {...props}
      resource={TRIGGER_MODEL}
      title={(context) => context.recordId === null
        ? t("triggers.new")
        : String((context.record as TriggerRecord | null)?.summary
          ?? (context.record as TriggerRecord | null)?.kind
          ?? t("triggers.saved"))}
      toolbarStart={(context) => <TriggerToolbar context={context} />}
      formExtras={(context) => <>
        <WorkflowEventConditionEditor context={context} />
        <TriggerRawConfig context={context} hasSchema={config.hasSchema} />
        <TriggerStatus context={context} />
      </>}
    >
      <Group label={t("triggers.details")} columns={2}>
        <Field name="workflow" createOnly readOnly />
        <Field name="kind" widget="select" options={kindOptions} createOnly required />
      </Group>
      {ruleFields.map((field) => <Field key={field.name} {...field} />)}
      <SlotOutlet entries={extensionFields} />
      <Field name="execution_actor" />
      <Group label={t("triggers.advanced")} columns={2} collapsible>
        {advancedFields.map((field) => <Field key={field.name} {...field} />)}
        <Field
          name="config"
          widget="json"
          label={t("triggers.ruleJson")}
          resolve={(values) => ({
            name: "config",
            widget: "json",
            label: t("triggers.ruleJson"),
            hidden: config.hasSchema(values.kind),
          })}
        />
      </Group>
    </Form>
  );
}

function TriggerRawConfig({
  context,
  hasSchema,
}: {
  context: RecordToolbarContext;
  hasSchema: (kind: unknown) => boolean;
}): React.ReactElement | null {
  const t = useWorkflowsT();
  const values = useFormViewValues(context.form);
  if (!hasSchema(values.kind)) return null;
  return (
    <Collapsible variant="section">
      <Collapsible.Trigger>
        <Collapsible.Icon />
        {t("triggers.ruleJson")}
      </Collapsible.Trigger>
      <Collapsible.Panel>
        <section aria-label={t("triggers.ruleJson")}>
          <JsonBlock value={values.config} />
        </section>
      </Collapsible.Panel>
    </Collapsible>
  );
}

type ScheduleMode = "interval" | "cron";

function ScheduleModeControl({ context }: { context: RecordToolbarContext }): React.ReactElement | null {
  const t = useWorkflowsT();
  const values = useFormViewValues(context.form) as TriggerRecord;
  if (String(values.kind ?? "").toLowerCase() !== "schedule") return null;
  const config = isJsonObject(values.config) ? values.config : {};
  const hasCron = Object.hasOwn(config, "cron");
  const hasInterval = Object.hasOwn(config, "interval_seconds");
  const mode = scheduleMode(config);
  return (
    <SegmentedControl
      aria-label={t("triggers.scheduleMode")}
      size="sm"
      value={mode}
      options={[
        { value: "interval", label: t("triggers.interval") },
        { value: "cron", label: t("triggers.cron") },
      ]}
      disabled={context.form.formReadOnly}
      onValueChange={(nextMode) => {
        const nextConfig = { ...config };
        if (nextMode === "interval") {
          delete nextConfig.cron;
          if (!hasInterval) nextConfig.interval_seconds = "";
        } else {
          delete nextConfig.interval_seconds;
          if (!hasCron) nextConfig.cron = "";
        }
        context.form.form.setValue("config", nextConfig, {
          shouldDirty: true,
          shouldTouch: true,
        });
      }}
    />
  );
}

function TriggerToolbar({ context }: { context: RecordToolbarContext }): React.ReactElement {
  const t = useWorkflowsT();
  const { close } = React.useContext(TriggerWorkflowContext);
  const [enableMutation, enableState] = useAuthoredMutation(EnableWorkflowTriggerDocument);
  const [disableMutation, disableState] = useAuthoredMutation(DisableWorkflowTriggerDocument);
  const toast = useToast();
  const record = context.record as TriggerRecord | null;
  const blocker = typeof record?.activation_blocker === "string" ? record.activation_blocker : undefined;
  const runLifecycle = async (enabled: boolean) => {
    if (!context.recordId) return;
    const request = enabled
      ? enableMutation({ trigger: context.recordId })
      : disableMutation({ trigger: context.recordId });
    const result = await request.catch((error) => {
      toast.danger({ title: errorMessage(error, t("triggers.conditionError")) });
      return null;
    });
    if (result === null) return;
    const outcome = extractActionOutcome(
      result,
      enabled ? "enable_workflow_trigger" : "disable_workflow_trigger",
    );
    if (!outcome) return;
    try {
      const message = runActionResult(outcome);
      if (message) toast.success({ title: message });
      context.reload();
    } catch (error) {
      toast.danger({
        title: enabled ? t("triggers.enable") : t("triggers.disable"),
        description: errorMessage(error, t("triggers.conditionError")),
      });
    }
  };
  return (
    <>
      <Button type="button" size="sm" variant="ghost" onClick={() => {
        void context.form.requestLeave().then((leave) => { if (leave) close(); });
      }}>
        {t("triggers.backToList")}
      </Button>
      <ScheduleModeControl context={context} />
      {record && !context.form.formReadOnly ? (
        <Button
          type="button"
          size="sm"
          disabled={!record.enabled && (context.form.formIsDirty || Boolean(blocker))}
          loading={enableState.fetching || disableState.fetching}
          title={!record.enabled && context.form.formIsDirty
            ? t("triggers.saveBeforeLifecycle")
            : !record.enabled ? blocker : undefined}
          onClick={() => { void runLifecycle(!Boolean(record.enabled)); }}
        >
          {record.enabled ? t("triggers.disable") : t("triggers.enable")}
        </Button>
      ) : null}
    </>
  );
}

function TriggerStatus({ context }: { context: RecordToolbarContext }): React.ReactElement | null {
  const t = useWorkflowsT();
  const { currentVersion } = React.useContext(TriggerWorkflowContext);
  const values = useFormViewValues(context.form) as TriggerRecord;
  const record = context.record as TriggerRecord | null;
  const current = { ...record, ...values };
  const isSchedule = String(current.kind ?? "").toLowerCase() === "schedule";
  const preview = useAuthoredQuery(
    WorkflowSchedulePreviewDocument,
    { config: current.config ?? {}, count: 3 },
    { enabled: isSchedule && current.config != null },
  );
  const blocker = typeof record?.activation_blocker === "string" ? record.activation_blocker : "";
  const result = preview.data?.workflow_schedule_preview;
  return (
    <section aria-label={t("triggers.status")} className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={record?.enabled ? "success" : "neutral"}>
          {record?.enabled ? t("triggers.enabled") : t("triggers.disabled")}
        </Badge>
        {typeof record?.summary === "string" && record.summary ? <span>{record.summary}</span> : null}
      </div>
      {blocker ? <ErrorBanner description={blocker} /> : null}
      {record && !record.enabled && !blocker && currentVersion != null ? (
        <p>{t("triggers.enableVersionExplanation", { version: currentVersion })}</p>
      ) : null}
      {record ? (
        <div>
          <strong>{t("triggers.activity")}</strong>
          <dl className="grid gap-1">
            <div><dt className="inline font-medium">{t("triggers.lastFire")}: </dt><dd className="inline">{formatOptionalUtc(record.last_fire_at, t("triggers.never"))}</dd></div>
            <div><dt className="inline font-medium">{t("triggers.nextFire")}: </dt><dd className="inline">{formatOptionalUtc(record.next_fire_at, t("triggers.none"))}</dd></div>
          </dl>
        </div>
      ) : null}
      {isSchedule ? (
        <div>
          <strong>{t("triggers.preview")}</strong>
          {preview.isFetching ? <p>{t("triggers.previewLoading")}</p> : null}
          {preview.error ? <ErrorBanner description={t("triggers.previewError")} /> : null}
          {result?.errors.map((message) => <ErrorBanner key={message} description={message} />)}
          {result ? (
            <>
              <p>{t("triggers.timezone", { timezone: result.timezone })}</p>
              <ol>{result.occurrences.map((occurrence) => (
                <li key={occurrence}>{formatUtc(occurrence)}</li>
              ))}</ol>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export const workflowTriggerForm = registerForm(TRIGGER_MODEL, WorkflowTriggerForm);

function WorkflowTriggerReadOnlyForm(props: RegisteredFormProps): React.ReactElement {
  return <WorkflowTriggerForm {...props} readOnly />;
}

export const workflowTriggerReadOnlyForm = registerForm(TRIGGER_MODEL, WorkflowTriggerReadOnlyForm);

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function scheduleMode(value: unknown): ScheduleMode | undefined {
  const config = isJsonObject(value) ? value : {};
  const hasCron = Object.hasOwn(config, "cron");
  const hasInterval = Object.hasOwn(config, "interval_seconds");
  return hasCron === hasInterval ? undefined : hasCron ? "cron" : "interval";
}

function formatUtc(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "UTC",
  }).format(new Date(value));
}

function formatOptionalUtc(value: unknown, fallback: string): string {
  return typeof value === "string" && value ? formatUtc(value) : fallback;
}
