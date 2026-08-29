import { extractActionOutcome, useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Button,
  DropdownMenu,
  Glyph,
  useActionResultRun,
  useRecordChromeContext,
} from "@angee/ui";
import * as React from "react";

import {
  RunWorkflowDocument,
  WorkflowsForSubjectDeclarationDocument,
} from "./documents.console";
import { useWorkflowsT } from "./i18n";

const WORKFLOW_MODEL = "workflows.Workflow";
const WORKFLOW_RUN_MODEL = "workflows.WorkflowRun";

/** Saved-record chrome declaration for workflows available to the current resource. */
export function RunWorkflowMenu(): React.ReactElement | null {
  const t = useWorkflowsT();
  const { resource, dataProviderName, recordId } = useRecordChromeContext();
  const query = useAuthoredQuery(
    WorkflowsForSubjectDeclarationDocument,
    { subjectDeclaration: resource },
    { dataProviderName, models: [WORKFLOW_MODEL] },
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
            {workflows.map((workflow) => (
              <DropdownMenu.Item
                key={workflow.id}
                disabled={startState.fetching}
                onClick={() =>
                  void settle(async () =>
                    extractActionOutcome(
                      await startWorkflow({
                        workflow: workflow.id,
                        subjectDeclaration: resource,
                        subjectId: recordId,
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
