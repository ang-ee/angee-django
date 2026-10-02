import { holdsPermission, type Row } from "@angee/metadata";
import { extractActionOutcome } from "@angee/refine";
import { OPEN_ROUND, CLOSE_ROUND, CANCEL_ROUND, TRANSFER_ROUND, ADMIT_RESPONDER, REMOVE_RESPONDER, WIDEN_ROUND } from "./documents";
import {
  ActionFormDialog,
  Button,
  Glyph,
  canonicalOptionValue,
  useAuthoredResourceMutation,
  useRecordAction,
  useActionResultRun,
  type ActionDescriptor,
  type WidgetOption,
} from "@angee/ui";
import * as React from "react";

import { useProposalsT } from "./i18n";
import { PROPOSAL_MODEL, ROUND_MODEL, USER_MODEL } from "./resources";

const CLOSE_OUTCOMES = [
  { value: "AWARDED", labelKey: "round.outcome.awarded" },
  { value: "NO_AWARD", labelKey: "round.outcome.noAward" },
] as const;
export type CloseOutcome = (typeof CLOSE_OUTCOMES)[number]["value"];

const OPENING_POLICY_LABELS: Record<string, string> = {
  FACILITATOR_ONLY: "round.policy.facilitatorOnly",
  ANSWERS: "round.policy.answers",
  ANSWERS_AND_TRACKS: "round.policy.answersAndTracks",
  DRAFTS_AND_TRACKS: "round.policy.draftsAndTracks",
};

export function openingPolicyLabelKey(value: unknown): string {
  return OPENING_POLICY_LABELS[String(value ?? "").toUpperCase()] ?? "round.action.open.unknown";
}

export interface RoundActionRow extends Row {
  id: string;
  opening_policy?: unknown;
  status?: unknown;
}

export interface RoundCeremonyActions {
  open: ActionDescriptor;
  close: ActionDescriptor;
  transfer: ActionDescriptor;
  cancel: ActionDescriptor;
  admit: ActionDescriptor;
  remove: ActionDescriptor;
  widen: ActionDescriptor;
}

/** Round lifecycle descriptors shared by the record form and comparison page. */
export function useRoundCeremonyActions(
  roundId: string,
  defaultOutcome?: CloseOutcome,
): RoundCeremonyActions {
  const t = useProposalsT();
  const closeOutcomeOptions = React.useMemo<readonly WidgetOption[]>(
    () => CLOSE_OUTCOMES.map(({ value, labelKey }) => ({
      value,
      label: t(labelKey),
    })),
    [t],
  );
  const options = { invalidateModels: [ROUND_MODEL, PROPOSAL_MODEL] };
  const [openRound] = useAuthoredResourceMutation(OPEN_ROUND, options);
  const [closeRound] = useAuthoredResourceMutation(CLOSE_ROUND, options);
  const [cancelRound] = useAuthoredResourceMutation(CANCEL_ROUND, options);
  const [transferRound] = useAuthoredResourceMutation(TRANSFER_ROUND, options);
  const [admit] = useAuthoredResourceMutation(ADMIT_RESPONDER, options);
  const [remove] = useAuthoredResourceMutation(REMOVE_RESPONDER, options);
  const [widen] = useAuthoredResourceMutation(WIDEN_ROUND, options);
  const settle = useActionResultRun();
  const open = useRecordAction(async (round, context) => {
    await settle(async () => extractActionOutcome(await openRound({ round, revision: recordRevision(context.record) }), "open_proposal_round"));
  });
  const cancel = useRecordAction(async (round, context) => {
    await settle(async () => extractActionOutcome(await cancelRound({ round, revision: recordRevision(context.record) }), "cancel_proposal_round"));
  });

  const closeSubmit = React.useCallback<
    NonNullable<ActionDescriptor["submit"]>
  >(
    async (values, context) => {
      const id = actionRecordId(context.record, t("round.action.failed"));
      return extractActionOutcome(await closeRound({
        round: id, revision: recordRevision(context.record),
        outcome: closeOutcome(closeOutcomeOptions, values.outcome, t("round.action.invalidOutcome")) as CloseOutcome,
        accepted: idList(values.accepted),
        partial: idList(values.partial),
      }), "close_proposal_round");
    },
    [closeOutcomeOptions, closeRound, t],
  );

  const transferSubmit = React.useCallback<
    NonNullable<ActionDescriptor["submit"]>
  >(
    async (values, context) => {
      const round = actionRecordId(context.record, t("round.action.failed"));
      return extractActionOutcome(await transferRound({
        round, revision: recordRevision(context.record),
        facilitator: requiredId(
          values.facilitator,
          t("round.action.invalidFacilitator"),
        ),
      }), "transfer_proposal_round_facilitation");
    },
    [t, transferRound],
  );

  return React.useMemo<RoundCeremonyActions>(
    () => ({
      admit: {
        id: "admit-responder", label: t("round.action.admit"),
        args: [
          { name: "responder", label: t("common.responder"), argKind: "relation", resource: USER_MODEL },
          { name: "track", label: t("round.action.track"), widget: "switch", defaultValue: false, optional: true },
        ],
        submit: async (values, context) => extractActionOutcome(await admit({
          round: actionRecordId(context.record, t("round.action.failed")),
          responder: requiredId(values.responder, t("round.action.responder")), track: values.track === true,
        }), "admit_proposal_round_responder"),
        visibleWhen: (record) => record.can_admit === true,
      },
      remove: {
        id: "remove-responder", label: t("round.action.remove"), danger: true,
        args: [{ name: "responder", label: t("common.responder"), argKind: "relation", resource: USER_MODEL }],
        submit: async (values, context) => extractActionOutcome(await remove({
          round: actionRecordId(context.record, t("round.action.failed")), revision: recordRevision(context.record),
          responder: requiredId(values.responder, t("round.action.responder")),
        }), "remove_proposal_round_responder"),
        visibleWhen: (record) => holdsPermission(record, "manage"),
      },
      widen: {
        id: "widen-round", label: t("round.action.widen"),
        args: [{ name: "policy", widget: "select", options: [
          "ANSWERS", "ANSWERS_AND_TRACKS", "DRAFTS_AND_TRACKS",
        ].map((value) => ({ value, label: t(openingPolicyLabelKey(value)) })) }],
        submit: async (values, context) => extractActionOutcome(await widen({
          round: actionRecordId(context.record, t("round.action.failed")), revision: recordRevision(context.record),
          policy: openingPolicyValue(values.policy),
        }), "widen_proposal_round_opening_policy"),
        visibleWhen: (record) => holdsPermission(record, "write") && isOpenedRound(record),
      },
      open: {
        id: "open-round",
        label: t("round.action.open"),
        icon: "proposals-open",
        run: open,
        confirm: (record) => ({
          title: t("round.action.openTitle"),
          body: t(openingPolicyMessageKey(record.opening_policy)),
        }),
        visibleWhen: (record) => holdsPermission(record, "write") && record.can_open === true,
      },
      close: {
        id: "close-round",
        label: t("round.action.close"),
        icon: "proposals-award",
        danger: true,
        args: [
          {
            name: "outcome",
            label: t("round.action.outcome"),
            widget: "select",
            options: closeOutcomeOptions,
            ...(defaultOutcome ? { defaultValue: defaultOutcome } : {}),
          },
          {
            name: "accepted",
            label: t("round.action.accepted"),
            argKind: "relationList",
            resource: PROPOSAL_MODEL,
            filters: submittedProposalFilters(roundId),
            fromContext: () => [],
            optional: true,
          },
          {
            name: "partial",
            label: t("round.action.partial"),
            argKind: "relationList",
            resource: PROPOSAL_MODEL,
            filters: submittedProposalFilters(roundId),
            fromContext: () => [],
            optional: true,
          },
        ],
        submit: closeSubmit,
        visibleWhen: (record) => holdsPermission(record, "write") && isOpenedRound(record),
      },
      transfer: {
        id: "transfer-round",
        label: t("round.action.transfer"),
        icon: "proposals-transfer",
        args: [
          {
            name: "facilitator",
            label: t("round.action.facilitator"),
            argKind: "relation",
            resource: USER_MODEL,
          },
        ],
        submit: transferSubmit,
        visibleWhen: (record) => holdsPermission(record, "write") && isNonTerminalRound(record),
      },
      cancel: {
        id: "cancel-round",
        label: t("round.action.cancel"),
        icon: "proposals-withdraw",
        danger: true,
        run: cancel,
        confirm: {
          title: t("round.action.cancelTitle"),
          body: t("round.action.cancelBody"),
          danger: true,
        },
        visibleWhen: (record) => holdsPermission(record, "write") && isNonTerminalRound(record),
      },
    }),
    [admit, remove, widen, cancel, closeOutcomeOptions, closeSubmit, defaultOutcome, open, roundId, t, transferSubmit],
  );
}

/** Award/no-award smart controls for the comparison header. */
export function RoundCloseControls({
  roundId,
  round,
}: {
  roundId: string;
  round: RoundActionRow;
}): React.ReactElement | null {
  const t = useProposalsT();
  const [defaultOutcome, setDefaultOutcome] =
    React.useState<CloseOutcome | undefined>();
  const [open, setOpen] = React.useState(false);
  const action = useRoundCeremonyActions(roundId, defaultOutcome).close;
  if (!isOpenedRound(round) || !holdsPermission(round, "write")) return null;

  const show = (outcome: CloseOutcome) => {
    setDefaultOutcome(outcome);
    setOpen(true);
  };
  return (
    <>
      <Button type="button" size="sm" variant="primary" onClick={() => show("AWARDED")}>
        <Glyph decorative name="proposals-award" />
        {t("round.action.award")}
      </Button>
      <Button type="button" size="sm" variant="secondary" onClick={() => show("NO_AWARD")}>
        {t("round.action.noAward")}
      </Button>
      {open ? (
        <ActionFormDialog
          key={`${action.id}:${defaultOutcome ?? ""}`}
          action={action}
          context={{ record: round, selectedIds: [] }}
          open
          onOpenChange={setOpen}
        />
      ) : null}
    </>
  );
}

export function openingPolicyMessageKey(value: unknown): string {
  switch (String(value ?? "").trim().toLowerCase()) {
    case "facilitator_only":
      return "round.action.open.facilitatorOnly";
    case "answers":
      return "round.action.open.answers";
    case "drafts_and_tracks":
      return "round.action.open.draftsAndTracks";
    case "answers_and_tracks":
      return "round.action.open.answersAndTracks";
    default:
      return "round.action.open.unknown";
  }
}

function submittedProposalFilters(roundId: string) {
  return [
    { field: "round", operator: "eq" as const, value: roundId },
    { field: "state", operator: "eq" as const, value: "SUBMITTED" },
  ];
}

function actionRecordId(record: Row | null, message: string): string {
  const value = record?.id;
  if (typeof value === "string" && value) return value;
  throw new TypeError(message);
}

function requiredId(value: unknown, message: string): string {
  if (typeof value === "string" && value) return value;
  throw new TypeError(message);
}

export function closeOutcome(
  options: readonly WidgetOption[], value: unknown, message: string,
): string {
  const outcome = canonicalOptionValue(options, value);
  if (outcome !== undefined) return outcome;
  throw new TypeError(message);
}

function roundState(record: Row): string {
  return String(record.status ?? "").trim().toLowerCase();
}

function isOpenedRound(record: Row): boolean {
  return roundState(record) === "opened";
}

function isNonTerminalRound(record: Row): boolean {
  return ["collecting", "opened"].includes(roundState(record));
}

export function recordRevision(record: Row | null): number | undefined {
  return typeof record?.revision === "number" ? record.revision : undefined;
}

function idList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
}

function openingPolicyValue(value: unknown): "ANSWERS" | "ANSWERS_AND_TRACKS" | "DRAFTS_AND_TRACKS" {
  if (value === "ANSWERS" || value === "ANSWERS_AND_TRACKS" || value === "DRAFTS_AND_TRACKS") return value;
  throw new Error("Choose an opening policy.");
}
