import * as React from "react";
import { updateRouteSearch, useActionResultRun, type ActionResultRun } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { workflowSubjectActionSearchPatch } from "./decision-navigation";

/** Settle an ActionResult and follow its returned WorkflowRun in the current subject aside. */
export function useWorkflowSubjectActionResult(): ActionResultRun {
  const settle = useActionResultRun();
  const navigate = useNavigate();
  return React.useCallback<ActionResultRun>(async (fire) => {
    const outcome = await settle(fire);
    if (outcome?.ok && outcome.id) {
      const runId = outcome.id;
      void navigate({
        to: ".",
        replace: true,
        search: updateRouteSearch(workflowSubjectActionSearchPatch(runId)),
      });
    }
    return outcome;
  }, [navigate, settle]);
}
