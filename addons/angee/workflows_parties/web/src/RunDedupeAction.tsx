import * as React from "react";
import {
  extractActionOutcome,
  useAuthoredMutation,
  useAuthoredQuery,
} from "@angee/refine";
import { Button, useActionResultRun } from "@angee/ui";

import { StartDedupeRun, SubjectlessWorkflows } from "./documents";
import { useWorkflowsPartiesT } from "./i18n";

/** The seeded workflow stable key — kept in lockstep with the install yaml. */
const DEDUPE_WORKFLOW_KEY = "dedupe_parties";

/**
 * The Review-toolbar launcher: starts one dedupe run and lands the user on its
 * run page, where the batch Decision appears in place (and in the workflows
 * inbox) as soon as the scan step completes.
 */
export function RunDedupeAction(): React.ReactElement | null {
  const t = useWorkflowsPartiesT();
  const workflows = useAuthoredQuery(
    SubjectlessWorkflows,
    { subjectDeclaration: "" },
    { models: ["workflows.Workflow"] },
  );
  const [start, { fetching }] = useAuthoredMutation(StartDedupeRun, {
    invalidateModels: ["workflows.WorkflowRun"],
  });
  const settle = useActionResultRun({
    linkTo: "workflows.WorkflowRun",
    noResultTitle: t("dedupe.failed"),
  });

  const dedupe = (workflows.data?.workflows_for_subject_declaration ?? []).find(
    (workflow) => workflow.key === DEDUPE_WORKFLOW_KEY,
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
      onClick={() =>
        void settle(async () =>
          extractActionOutcome(
            await start({ id: dedupe.id }),
            "start_workflow_run",
          ),
        )
      }
    >
      {fetching ? t("dedupe.running") : t("dedupe.run")}
    </Button>
  );
}
