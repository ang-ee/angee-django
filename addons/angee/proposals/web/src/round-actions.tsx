import type { Row } from "@angee/metadata";
import {
  extractActionOutcome,
  type ActionOutcome,
  type DocumentVariables,
} from "@angee/refine";
import {
  ActionFormDialog,
  Button,
  Glyph,
  useActionResultRun,
  useAuthoredResourceMutation,
  useRecordAction,
  type ActionDescriptor,
  type RecordAction,
} from "@angee/ui";
import * as React from "react";

import {
  CancelProposalRoundDocument,
  CloseProposalRoundDocument,
  OpenProposalRoundDocument,
  TransferProposalRoundDocument,
} from "./documents";
import { useProposalsT } from "./i18n";
import { PROPOSAL_MODEL, ROUND_MODEL, USER_MODEL } from "./resources";

type CancelVariables = DocumentVariables<typeof CancelProposalRoundDocument>;
type CloseVariables = DocumentVariables<typeof CloseProposalRoundDocument>;
type OpenVariables = DocumentVariables<typeof OpenProposalRoundDocument>;
type TransferVariables = DocumentVariables<typeof TransferProposalRoundDocument>;

export type CloseOutcome = CloseVariables["outcome"];

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
}

/** Round lifecycle descriptors shared by the record form and comparison page. */
export function useRoundCeremonyActions(
  roundId: string,
  defaultOutcome?: CloseOutcome,
): RoundCeremonyActions {
  const t = useProposalsT();
  const settle = useActionResultRun();
  const [openRound] = useAuthoredResourceMutation(OpenProposalRoundDocument, {
    invalidateModels: [ROUND_MODEL, PROPOSAL_MODEL],
    shouldInvalidate: (data) => data?.open_proposal_round.ok === true,
  });
  const [closeRound] = useAuthoredResourceMutation(CloseProposalRoundDocument, {
    invalidateModels: [ROUND_MODEL, PROPOSAL_MODEL],
    shouldInvalidate: (data) => data?.close_proposal_round.ok === true,
  });
  const [cancelRound] = useAuthoredResourceMutation(CancelProposalRoundDocument, {
    invalidateModels: [ROUND_MODEL, PROPOSAL_MODEL],
    shouldInvalidate: (data) => data?.cancel_proposal_round.ok === true,
  });
  const [transferRound] = useAuthoredResourceMutation(
    TransferProposalRoundDocument,
    {
      invalidateModels: [ROUND_MODEL, PROPOSAL_MODEL],
      shouldInvalidate: (data) =>
        data?.transfer_proposal_round_facilitation.ok === true,
    },
  );

  const fireOpen = React.useCallback(
    async (id: string): Promise<ActionOutcome | undefined> =>
      extractActionOutcome(
        await openRound({ round: id } satisfies OpenVariables),
        "open_proposal_round",
      ) ?? undefined,
    [openRound],
  );
  const open = useSettledRecordAction(fireOpen, settle);

  const fireCancel = React.useCallback(
    async (id: string): Promise<ActionOutcome | undefined> =>
      extractActionOutcome(
        await cancelRound({ round: id } satisfies CancelVariables),
        "cancel_proposal_round",
      ) ?? undefined,
    [cancelRound],
  );
  const cancel = useSettledRecordAction(fireCancel, settle);

  const closeSubmit = React.useCallback<
    NonNullable<ActionDescriptor["submit"]>
  >(
    async (values, context) => {
      const id = actionRecordId(context.record, t("round.action.failed"));
      const variables = {
        round: id,
        outcome: closeOutcome(values.outcome, t("round.action.invalidOutcome")),
        accepted: idList(values.accepted),
        partial: idList(values.partial),
      } satisfies CloseVariables;
      const data = await closeRound(variables);
      return (
        extractActionOutcome(data, "close_proposal_round") ?? {
          ok: false,
          message: t("round.action.failed"),
        }
      );
    },
    [closeRound, t],
  );

  const transferSubmit = React.useCallback<
    NonNullable<ActionDescriptor["submit"]>
  >(
    async (values, context) => {
      const variables = {
        round: actionRecordId(context.record, t("round.action.failed")),
        facilitator: requiredId(
          values.facilitator,
          t("round.action.invalidFacilitator"),
        ),
      } satisfies TransferVariables;
      const data = await transferRound(variables);
      return (
        extractActionOutcome(
          data,
          "transfer_proposal_round_facilitation",
        ) ?? { ok: false, message: t("round.action.failed") }
      );
    },
    [t, transferRound],
  );

  return React.useMemo(
    () => ({
      open: {
        id: "open-round",
        label: t("round.action.open"),
        icon: "proposals-open",
        run: open,
        confirm: (record) => ({
          title: t("round.action.openTitle"),
          body: t(openingPolicyMessageKey(record.opening_policy)),
        }),
        visibleWhen: isCollectingRound,
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
            options: [
              { value: "AWARDED", label: t("round.outcome.awarded") },
              { value: "NO_AWARD", label: t("round.outcome.noAward") },
            ],
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
        visibleWhen: isOpenedRound,
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
        visibleWhen: isNonTerminalRound,
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
        visibleWhen: isNonTerminalRound,
      },
    }),
    [cancel, closeSubmit, defaultOutcome, open, roundId, t, transferSubmit],
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
  if (!isOpenedRound(round)) return null;

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

function useSettledRecordAction(
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

export function openingPolicyMessageKey(value: unknown): string {
  switch (String(value ?? "").trim().toLowerCase()) {
    case "facilitator_only":
      return "round.action.open.facilitatorOnly";
    case "answers":
      return "round.action.open.answers";
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

function idList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item !== "")
    : [];
}

function closeOutcome(value: unknown, message: string): CloseOutcome {
  if (value === "AWARDED" || value === "NO_AWARD") return value;
  throw new TypeError(message);
}

function roundState(record: Row): string {
  return String(record.status ?? "").trim().toLowerCase();
}

function isCollectingRound(record: Row): boolean {
  return roundState(record) === "collecting";
}

export function isOpenedRound(record: Row): boolean {
  return roundState(record) === "opened";
}

function isNonTerminalRound(record: Row): boolean {
  return ["collecting", "opened"].includes(roundState(record));
}
