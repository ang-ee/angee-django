import {
  extractActionOutcome,
  useAuthoredMutation,
  useAuthoredQuery,
  type ActionOutcome,
  type AuthoredMutate,
} from "@angee/refine";
import type { DocumentType } from "@angee/gql/console";
import {
  Button,
  DropdownMenu,
  Glyph,
  MutationDialog,
  mutationDialogValueCodecs,
  useActionResultRun,
  useRecordChromeContext,
  type MutationDialogField,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import {
  RunWorkflowDocument,
  WorkflowLaunchDocument,
  WorkflowsForSubjectDeclarationDocument,
} from "./documents.console";
import { useWorkflowsT } from "./i18n";

const WORKFLOW_MODEL = "workflows.Workflow";
const WORKFLOW_RUN_MODEL = "workflows.WorkflowRun";

/** Workflow editor records whose chrome must never advertise record automations. */
export const WORKFLOW_TECHNICAL_MODELS: ReadonlySet<string> = new Set([
  WORKFLOW_MODEL,
  "workflows.Step",
  "workflows.Edge",
  "workflows.Trigger",
  WORKFLOW_RUN_MODEL,
  "workflows.StepRun",
  "workflows.Decision",
]);

/** Saved-record chrome declaration for workflows available to the current resource. */
export function RunWorkflowMenu(): React.ReactElement | null {
  const t = useWorkflowsT();
  const { resource, dataProviderName, recordId } = useRecordChromeContext();
  const isWorkflowDefinition = resource === WORKFLOW_MODEL;
  const acceptsRecordAutomations = !WORKFLOW_TECHNICAL_MODELS.has(resource);
  const query = useAuthoredQuery(
    WorkflowsForSubjectDeclarationDocument,
    { subjectDeclaration: resource },
    { dataProviderName, enabled: acceptsRecordAutomations, models: [WORKFLOW_MODEL] },
  );
  const launchQuery = useAuthoredQuery(
    WorkflowLaunchDocument,
    { id: recordId },
    { dataProviderName, enabled: isWorkflowDefinition, models: [WORKFLOW_MODEL] },
  );
  const [startWorkflow, startState] = useAuthoredMutation(RunWorkflowDocument, {
    dataProviderName,
    invalidateModels: [WORKFLOW_RUN_MODEL],
    shouldInvalidate: (data) => data?.start_workflow_run.ok === true,
  });
  const settle = useActionResultRun({
    linkTo: WORKFLOW_RUN_MODEL,
    noResultTitle: t("runWorkflow.failed"),
  });
  const workflows = query.data?.workflows_for_subject_declaration ?? [];

  if (isWorkflowDefinition) {
    return (
      <CurrentWorkflowLaunch
        workflow={launchQuery.data?.workflows_by_pk ?? null}
        loading={launchQuery.isFetching}
        startState={startState}
        startWorkflow={startWorkflow}
        settle={settle}
      />
    );
  }
  if (!acceptsRecordAutomations) return null;
  if (workflows.length === 0) return null;

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        render={
          <Button type="button" variant="ghost" size="md" loading={startState.fetching}>
            <Glyph name="workflow-run" />
            {t("runWorkflow.label")}
            <Glyph decorative name="chevron-down" className="size-3" />
          </Button>
        }
      />
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner sideOffset={6} align="start">
          <DropdownMenu.Content className="w-56">
            <DropdownMenu.Label>{t("runWorkflow.menuPurpose")}</DropdownMenu.Label>
            {workflows.map((workflow) => (
              <DropdownMenu.Item
                key={workflow.id}
                disabled={startState.fetching}
                onClick={() =>
                  void settle(async () =>
                    extractActionOutcome(
                      await startWorkflow({
                        workflow: workflow.id,
                        subject: { subject_declaration: resource, id: recordId },
                      }),
                      "start_workflow_run",
                    ),
                  )
                }
              >
                <Glyph name="workflow-run" />
                {workflow.name}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

type WorkflowLaunchProjection = NonNullable<
  DocumentType<typeof WorkflowLaunchDocument>["workflows_by_pk"]
>;

export function CurrentWorkflowLaunch({
  workflow,
  loading,
  startState,
  startWorkflow,
  settle,
}: {
  workflow: WorkflowLaunchProjection | null;
  loading: boolean;
  startState: { fetching: boolean };
  startWorkflow: AuthoredMutate<typeof RunWorkflowDocument>;
  settle: ReturnType<typeof useActionResultRun>;
}): React.ReactElement | null {
  const t = useWorkflowsT();
  const [open, setOpen] = React.useState(false);
  const unavailableDescriptionId = React.useId();
  const isAutomation = workflow?.purpose === "AUTOMATION";
  const publishedId = workflow?.current_published_id ?? null;
  const publishedVersion = workflow?.current_published_version ?? null;
  const subjectDeclaration = workflow?.current_published_subject_declaration ?? null;
  const needsSubject = Boolean(subjectDeclaration);
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => subjectDeclaration ? [{
      name: "subject",
      label: t("runWorkflow.subjectLabel"),
      description: t("runWorkflow.subjectDescription", { subject: subjectDeclaration }),
      required: true,
      relation: { resource: subjectDeclaration },
    }] : [],
    [subjectDeclaration, t],
  );
  const description = publishedVersion === null
    ? t("runWorkflow.currentUnavailable")
    : workflow?.status === "DRAFT"
      ? t("runWorkflow.currentDraftDescription", { version: publishedVersion })
      : t("runWorkflow.currentPublishedDescription", { version: publishedVersion });
  const run = React.useCallback(async (subjectId?: string) => {
    if (!publishedId) return undefined;
    return settle(async () => extractActionOutcome(
      await startWorkflow({
        workflow: publishedId,
        ...(subjectDeclaration && subjectId
          ? { subject: { subject_declaration: subjectDeclaration, id: subjectId } }
          : {}),
      }),
      "start_workflow_run",
    ));
  }, [publishedId, settle, startWorkflow, subjectDeclaration]);

  if (workflow && !isAutomation) return null;

  return (
    <>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="md"
          loading={loading || startState.fetching}
          disabled={publishedId === null}
          aria-describedby={publishedId === null ? unavailableDescriptionId : undefined}
          onClick={() => setOpen(true)}
        >
          <Glyph name="workflow-run" />
          {t("form.start")}
        </Button>
        {publishedId === null && !loading ? (
          <span id={unavailableDescriptionId} className="text-xs text-fg-muted">
            {description}
          </span>
        ) : null}
      </div>
      <MutationDialog<{ subjectId?: string }, ActionOutcome | undefined>
        open={open}
        onOpenChange={setOpen}
        title={t("runWorkflow.currentTitle")}
        description={description}
        fields={fields}
        submitLabel={t("runWorkflow.submit")}
        submittingLabel={t("runWorkflow.submitting")}
        errorFallback={t("runWorkflow.failed")}
        parseValues={needsSubject ? parseSubjectValues : parseSubjectlessValues}
        onSubmit={({ subjectId }) => run(subjectId)}
        closeOnSubmit={false}
        onSubmitted={(outcome) => {
          if (outcome?.ok) setOpen(false);
        }}
      />
    </>
  );
}

function parseSubjectValues(values: MutationDialogValues): { subjectId?: string } {
  return {
    subjectId: mutationDialogValueCodecs.requiredString(values.subject, "subject"),
  };
}

function parseSubjectlessValues(): { subjectId?: undefined } {
  return {};
}
