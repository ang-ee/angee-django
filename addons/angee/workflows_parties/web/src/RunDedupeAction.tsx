import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { Button, useToast } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { StartDedupeRun, SubjectlessWorkflows } from "./documents";
import { useWorkflowsPartiesT } from "./i18n";

/** The seeded workflow's display name — kept in lockstep with the install yaml. */
export const DEDUPE_WORKFLOW_NAME = "Deduplicate contacts";

/**
 * The Review-toolbar launcher: starts one dedupe run and lands the user on its
 * run page, where the batch Decision appears in place (and in the workflows
 * inbox) as soon as the scan step completes.
 */
export function RunDedupeAction(): React.ReactElement | null {
  const t = useWorkflowsPartiesT();
  const toast = useToast();
  const navigate = useNavigate();
  const workflows = useAuthoredQuery(
    SubjectlessWorkflows,
    { subjectDeclaration: "" },
    { models: ["workflows.Workflow"] },
  );
  const [start, { fetching }] = useAuthoredMutation(StartDedupeRun, {
    invalidateModels: ["workflows.WorkflowRun"],
  });

  const dedupe = (workflows.data?.workflows_for_subject_declaration ?? []).find(
    (workflow) => workflow.name === DEDUPE_WORKFLOW_NAME,
  );
  // No published dedupe workflow reachable by this actor: contribute nothing
  // rather than a dead button.
  if (!dedupe) return null;

  return (
    <Button
      variant="secondary"
      size="sm"
      disabled={fetching}
      title={t("dedupe.description")}
      onClick={() => {
        void (async () => {
          const data = await start({ id: dedupe.id });
          const result = data?.start_workflow_run;
          if (!result?.ok || !result.id) {
            toast.danger({ title: result?.message || t("dedupe.failed") });
            return;
          }
          void navigate({ to: `/workflows/runs/${result.id}` });
        })();
      }}
    >
      {fetching ? t("dedupe.running") : t("dedupe.run")}
    </Button>
  );
}
