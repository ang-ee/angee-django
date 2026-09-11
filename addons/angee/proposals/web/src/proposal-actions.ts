import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  useRecordActionMutation,
  type ActionDescriptor,
} from "@angee/ui";
import { PROJECT_MODEL } from "@angee/projects";
import * as React from "react";

import { useProposalsT } from "./i18n";
import { PROPOSAL_MODEL, ROUND_MODEL } from "./resources";

/** Proposal lifecycle and private-track actions for the responder record form. */
export function useProposalCeremonyActions(): readonly ActionDescriptor[] {
  const t = useProposalsT();
  const proposalArgument = React.useCallback((proposal: string) => ({ proposal }), []);
  const [submitRun] = useRecordActionMutation<ActionFieldName>("submit_proposal", {
    invalidateModels: [PROPOSAL_MODEL, ROUND_MODEL],
    actionArguments: proposalArgument,
    settle: { noResultTitle: t("proposal.action.failed") },
  });
  const [withdrawRun] = useRecordActionMutation<ActionFieldName>("withdraw_proposal", {
    invalidateModels: [PROPOSAL_MODEL, ROUND_MODEL],
    actionArguments: proposalArgument,
    settle: { noResultTitle: t("proposal.action.failed") },
  });
  const [createTrackRun] = useRecordActionMutation<ActionFieldName>("create_proposal_track", {
    invalidateModels: [PROPOSAL_MODEL, PROJECT_MODEL],
    actionArguments: proposalArgument,
    linkTo: PROJECT_MODEL,
    settle: { noResultTitle: t("proposal.action.failed") },
  });
  const [publishTrackRun] = useRecordActionMutation<ActionFieldName>("publish_proposal_track", {
    invalidateModels: [PROPOSAL_MODEL, PROJECT_MODEL],
    actionArguments: proposalArgument,
    linkTo: PROJECT_MODEL,
    settle: { noResultTitle: t("proposal.action.failed") },
  });

  return React.useMemo(
    () => [
      {
        id: "submit-proposal",
        label: t("proposal.action.submit"),
        icon: "proposals-submit",
        run: submitRun,
        visibleWhen: (record) => proposalState(record) === "draft",
      },
      {
        id: "withdraw-proposal",
        label: t("proposal.action.withdraw"),
        icon: "proposals-withdraw",
        danger: true,
        run: withdrawRun,
        visibleWhen: (record) => proposalState(record) === "submitted",
      },
      {
        id: "create-proposal-track",
        label: t("proposal.action.createTrack"),
        icon: "projects",
        run: createTrackRun,
        visibleWhen: (record) => record.track == null,
      },
      {
        id: "publish-proposal-track",
        label: t("proposal.action.publishTrack"),
        icon: "proposals-publish",
        run: publishTrackRun,
        visibleWhen: (record) => record.track != null,
      },
    ],
    [createTrackRun, publishTrackRun, submitRun, t, withdrawRun],
  );
}

function proposalState(record: Record<string, unknown>): string {
  return String(record.state ?? "").trim().toLowerCase();
}
