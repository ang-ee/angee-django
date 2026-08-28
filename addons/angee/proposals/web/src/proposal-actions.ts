import {
  extractActionOutcome,
  type ActionOutcome,
  type DocumentVariables,
} from "@angee/refine";
import {
  useActionResultRun,
  useAuthoredResourceMutation,
  useRecordAction,
  type ActionDescriptor,
  type RecordAction,
} from "@angee/ui";
import { PROJECT_MODEL } from "@angee/projects";
import * as React from "react";

import {
  CreateProposalTrackDocument,
  PublishProposalTrackDocument,
  SubmitProposalDocument,
  WithdrawProposalDocument,
} from "./documents";
import { useProposalsT } from "./i18n";
import { PROPOSAL_MODEL, ROUND_MODEL } from "./resources";

type SubmitVariables = DocumentVariables<typeof SubmitProposalDocument>;
type WithdrawVariables = DocumentVariables<typeof WithdrawProposalDocument>;
type CreateTrackVariables = DocumentVariables<typeof CreateProposalTrackDocument>;
type PublishTrackVariables = DocumentVariables<typeof PublishProposalTrackDocument>;

/** Proposal lifecycle and private-track actions for the responder record form. */
export function useProposalCeremonyActions(): readonly ActionDescriptor[] {
  const t = useProposalsT();
  const settle = useActionResultRun();
  const settleTrack = useActionResultRun({ linkTo: PROJECT_MODEL });
  const [submit] = useAuthoredResourceMutation(SubmitProposalDocument, {
    invalidateModels: [PROPOSAL_MODEL, ROUND_MODEL],
    shouldInvalidate: (data) => data?.submit_proposal.ok === true,
  });
  const [withdraw] = useAuthoredResourceMutation(WithdrawProposalDocument, {
    invalidateModels: [PROPOSAL_MODEL, ROUND_MODEL],
    shouldInvalidate: (data) => data?.withdraw_proposal.ok === true,
  });
  const [createTrack] = useAuthoredResourceMutation(CreateProposalTrackDocument, {
    invalidateModels: [PROPOSAL_MODEL, PROJECT_MODEL],
    shouldInvalidate: (data) => data?.create_proposal_track.ok === true,
  });
  const [publishTrack] = useAuthoredResourceMutation(PublishProposalTrackDocument, {
    invalidateModels: [PROPOSAL_MODEL, PROJECT_MODEL],
    shouldInvalidate: (data) => data?.publish_proposal_track.ok === true,
  });

  const submitRun = useSettledProposalAction(
    React.useCallback(
      async (id) =>
        extractActionOutcome(
          await submit({ proposal: id } satisfies SubmitVariables),
          "submit_proposal",
        ) ?? { ok: false, message: t("proposal.action.failed") },
      [submit, t],
    ),
    settle,
  );
  const withdrawRun = useSettledProposalAction(
    React.useCallback(
      async (id) =>
        extractActionOutcome(
          await withdraw({ proposal: id } satisfies WithdrawVariables),
          "withdraw_proposal",
        ) ?? { ok: false, message: t("proposal.action.failed") },
      [t, withdraw],
    ),
    settle,
  );
  const createTrackRun = useSettledProposalAction(
    React.useCallback(
      async (id) =>
        extractActionOutcome(
          await createTrack({ proposal: id } satisfies CreateTrackVariables),
          "create_proposal_track",
        ) ?? { ok: false, message: t("proposal.action.failed") },
      [createTrack, t],
    ),
    settleTrack,
  );
  const publishTrackRun = useSettledProposalAction(
    React.useCallback(
      async (id) =>
        extractActionOutcome(
          await publishTrack({ proposal: id } satisfies PublishTrackVariables),
          "publish_proposal_track",
        ) ?? { ok: false, message: t("proposal.action.failed") },
      [publishTrack, t],
    ),
    settleTrack,
  );

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

function useSettledProposalAction(
  fire: (id: string) => Promise<ActionOutcome | undefined>,
  settle: ReturnType<typeof useActionResultRun>,
): RecordAction {
  return useRecordAction(
    React.useCallback(
      async (id, context) => {
        const outcome = await settle(() => fire(id));
        if (outcome?.ok) context.refresh();
      },
      [fire, settle],
    ),
    { refresh: false },
  );
}

function proposalState(record: Record<string, unknown>): string {
  return String(record.state ?? "").trim().toLowerCase();
}
