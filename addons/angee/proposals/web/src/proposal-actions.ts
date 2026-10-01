import { extractActionOutcome } from "@angee/refine";
import { SUBMIT_PROPOSAL, WITHDRAW_PROPOSAL, PUBLISH_TRACK } from "./documents";
import { holdsPermission } from "@angee/metadata";
import { recordRevision } from "./round-actions";
import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  useRecordActionMutation,
  useAuthoredResourceMutation,
  useActionResultRun,
  useRecordAction,
  type ActionDescriptor,
} from "@angee/ui";
import { PROJECT_MODEL } from "@angee/projects";
import * as React from "react";

import { useProposalsT } from "./i18n";
import { PROPOSAL_MODEL, ROUND_MODEL } from "./resources";

/** Proposal lifecycle and private-track actions for the responder record form. */
export function useProposalCeremonyActions(): readonly ActionDescriptor[] {
  const t = useProposalsT();
  const options = { invalidateModels: [PROPOSAL_MODEL, ROUND_MODEL, PROJECT_MODEL] };
  const [submit] = useAuthoredResourceMutation(SUBMIT_PROPOSAL, options);
  const [withdraw] = useAuthoredResourceMutation(WITHDRAW_PROPOSAL, options);
  const [publish] = useAuthoredResourceMutation(PUBLISH_TRACK, options);
  const settle = useActionResultRun({ noResultTitle: t("proposal.action.failed") });
  const settleTrack = useActionResultRun({ linkTo: PROJECT_MODEL, noResultTitle: t("proposal.action.failed") });
  const submitRun = useRecordAction(async (proposal, context) => {
    await settle(async () => extractActionOutcome(await submit({ proposal, revision: recordRevision(context.record) }), "submit_proposal"));
  });
  const withdrawRun = useRecordAction(async (proposal, context) => {
    await settle(async () => extractActionOutcome(await withdraw({ proposal, revision: recordRevision(context.record) }), "withdraw_proposal"));
  });
  const publishTrackRun = useRecordAction(async (proposal, context) => {
    await settleTrack(async () => extractActionOutcome(await publish({ proposal, revision: recordRevision(context.record) }), "publish_proposal_track"));
  });
  const [createTrackRun] = useRecordActionMutation<ActionFieldName>("create_proposal_track", {
    invalidateModels: [PROPOSAL_MODEL, PROJECT_MODEL],
    idArgument: "proposal",
    linkTo: PROJECT_MODEL,
    settle: { noResultTitle: t("proposal.action.failed") },
  });

  return React.useMemo<readonly ActionDescriptor[]>(
    () => [
      {
        id: "submit-proposal",
        label: t("proposal.action.submit"),
        icon: "proposals-submit",
        run: submitRun,
        visibleWhen: (record) => holdsPermission(record, "write") && proposalState(record) === "draft",
      },
      {
        id: "withdraw-proposal",
        label: t("proposal.action.withdraw"),
        icon: "proposals-withdraw",
        danger: true,
        run: withdrawRun,
        visibleWhen: (record) => holdsPermission(record, "withdraw") && proposalState(record) === "submitted",
      },
      {
        id: "create-proposal-track",
        label: t("proposal.action.createTrack"),
        icon: "projects",
        run: createTrackRun,
        visibleWhen: (record) => holdsPermission(record, "write") && record.track == null,
      },
      {
        id: "publish-proposal-track",
        label: t("proposal.action.publishTrack"),
        icon: "proposals-publish",
        run: publishTrackRun,
        visibleWhen: (record) => holdsPermission(record, "publish") && record.track != null,
      },
    ],
    [createTrackRun, publishTrackRun, submitRun, t, withdrawRun],
  );
}

function proposalState(record: Record<string, unknown>): string {
  return String(record.state ?? "").trim().toLowerCase();
}
