import {
  EmptyState, RecordActionBar, RowsListView,
  useDescriptorRowActions, useRecordChromeContext,
} from "@angee/ui";
import type { Row } from "@angee/metadata";
import type { ReactElement } from "react";

import { useRoundComparisonData } from "./comparison-data";
import { RoundComparisonBody } from "./comparison-body";
import { useProposalsT } from "./i18n";
import { useRoundCeremonyActions } from "./round-actions";

interface RoundRecord extends Row {
  id: string;
  name: string;
  status: string;
  opening_policy: string;
  permissions: readonly string[];
  can_open: boolean;
  revision: number;
  roster: readonly { user: string | null; name: string; track_status: string }[];
}
type RosterRow = RoundRecord["roster"][number] & { id: string };
type RoundSurface = "primary" | "actions" | "people" | "approach";

/** Project identity is resolved to its active round by the server owner. */
export function ProjectRoundRecord({ surface }: { surface: RoundSurface }): ReactElement | null {
  const { record } = useRecordChromeContext();
  return <RoundRecordSurface surface={surface} round={roundRecord(record?.active_proposal_round)} />;
}

/** Round forms inherit these contributions regardless of the mounting route. */
export function RoundRecordSection({ surface }: { surface: RoundSurface }): ReactElement | null {
  const { record } = useRecordChromeContext();
  return <RoundRecordSurface surface={surface} round={roundRecord(record)} />;
}

function roundRecord(value: unknown): RoundRecord | null {
  return value && typeof value === "object" && typeof (value as RoundRecord).id === "string"
    ? value as RoundRecord : null;
}

function RoundRecordSurface({ surface, round }: {
  surface: RoundSurface;
  round: RoundRecord | null;
}): ReactElement | null {
  const t = useProposalsT();
  if (!round) return (surface === "actions" || surface === "primary") ? null :
    <EmptyState title={t("round.record.empty")} />;
  if (surface === "people") return <RoundPeople round={round} />;
  if (surface === "approach") return <RoundApproach roundId={round.id} />;
  return <RoundVerbs round={round} primary={surface === "primary"} />;
}

function RoundVerbs({ round, primary }: { round: RoundRecord; primary: boolean }): ReactElement {
  const actions = useRoundCeremonyActions(round.id);
  return <RecordActionBar record={round} actions={primary ? [{ ...actions.open, placement: "toolbar", primary: true }] : Object.values(actions).filter((action) =>
    ![actions.open.id, actions.admit.id, actions.remove.id].includes(action.id))} />;
}

function RoundPeople({ round }: { round: RoundRecord }): ReactElement {
  const t = useProposalsT();
  const actions = useRoundCeremonyActions(round.id);
  const remove = { ...actions.remove, confirm: (row: Row) => ({
    title: t("round.action.removeTitle", { name: String(row.name ?? "") }),
    body: t(row.track_status ? "round.action.removeWithTrack" : "round.action.removeBody"),
    danger: true,
  }) };
  const rowActions = useDescriptorRowActions<RosterRow>([remove], {
    visible: (_action, row) => Boolean(row.user),
    contextRecord: () => round,
    valuesFromRow: (_action, row) => ({ responder: row.user }),
  });
  return <><RowsListView<RosterRow>
    presentation="embedded"
    rows={round.roster.map((person, index) => ({ ...person, id: person.user ?? `roster-${index}` }))}
    columns={[
      { field: "name", header: t("common.responder") },
      { field: "track_status", header: t("common.status"), widget: "statusBadge" },
    ]}
    toolbarActions={<RecordActionBar record={round} actions={[{ ...actions.admit, placement: "toolbar" }]} />}
    rowActions={rowActions.rowActions}
  />{rowActions.dialog}</>;
}

function RoundApproach({ roundId }: { roundId: string }): ReactElement {
  const data = useRoundComparisonData(roundId);
  return <RoundComparisonBody data={data} />;
}
