import type { DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
import {
  EmptyState, ErrorBanner, LoadingPanel, RecordActionBar, RowsListView,
  defineRowAction, useActionResultRun, useRecordChromeContext,
} from "@angee/ui";
import type { ReactElement } from "react";

import { useRoundComparisonData } from "./comparison-data";
import { RoundComparisonGrid } from "./comparison-grid";
import { PROJECT_ROUND, RECORD_ROUND, ROUND_RECORD } from "./documents";
import { useProposalsT } from "./i18n";
import { PROPOSAL_MODEL, ROUND_MODEL } from "./resources";
import { useRoundCeremonyActions } from "./round-actions";

type RoundRecord = DocumentType<typeof ROUND_RECORD>;
type RosterRow = RoundRecord["roster"][number] & { id: string };
type RoundSurface = "primary" | "actions" | "people" | "approach";

/** Project identity is resolved to its active round by the server owner. */
export function ProjectRoundRecord({ surface }: { surface: RoundSurface }): ReactElement | null {
  const { recordId, dataProviderName } = useRecordChromeContext();
  const query = useAuthoredQuery(PROJECT_ROUND, { project: recordId }, {
    dataProviderName, models: [ROUND_MODEL, PROPOSAL_MODEL, "projects.Project"],
  });
  return <RoundRecordSurface surface={surface} round={query.data?.active_proposal_round}
    fetching={query.fetching} error={query.error} />;
}

/** Round forms inherit these contributions regardless of the mounting route. */
export function RoundRecordSection({ surface }: { surface: RoundSurface }): ReactElement | null {
  const { recordId, dataProviderName } = useRecordChromeContext();
  const query = useAuthoredQuery(RECORD_ROUND, { id: recordId }, {
    dataProviderName, models: [ROUND_MODEL, PROPOSAL_MODEL],
  });
  return <RoundRecordSurface surface={surface} round={query.data?.proposal_rounds_by_pk}
    fetching={query.fetching} error={query.error} />;
}

function RoundRecordSurface({ surface, round, fetching, error }: {
  surface: RoundSurface;
  round: RoundRecord | null | undefined;
  fetching: boolean;
  error: Error | null | undefined;
}): ReactElement | null {
  const t = useProposalsT();
  if (error) return <ErrorBanner title={t("round.record.error")} description={error.message} />;
  if (!round) return (surface === "actions" || surface === "primary") ? null : fetching ? <LoadingPanel /> :
    <EmptyState title={t("round.record.empty")} />;
  if (surface === "people") return <RoundPeople round={round} />;
  if (surface === "approach") return <RoundApproach roundId={round.id} />;
  return <RoundVerbs round={round} primary={surface === "primary"} />;
}

function RoundVerbs({ round, primary }: { round: RoundRecord; primary: boolean }): ReactElement {
  const actions = useRoundCeremonyActions(round.id);
  return <RecordActionBar record={round} actions={primary ? [{ ...actions.open, placement: "toolbar" }] : Object.values(actions).filter((action) => action.id !== actions.open.id)} />;
}

function RoundPeople({ round }: { round: RoundRecord }): ReactElement {
  const t = useProposalsT();
  const actions = useRoundCeremonyActions(round.id);
  const settle = useActionResultRun();
  return <RowsListView<RosterRow>
    presentation="embedded"
    rows={round.roster.map((person, index) => ({ ...person, id: person.user ?? `roster-${index}` }))}
    columns={[
      { field: "name", header: t("common.responder") },
      { field: "track_status", header: t("common.status"), widget: "statusBadge" },
    ]}
    toolbarActions={<RecordActionBar record={round} actions={[{ ...actions.admit, placement: "toolbar" }]} />}
    rowActions={[defineRowAction<RosterRow>({
      kind: "page", id: "remove-responder", label: t("round.action.remove"),
      variant: "danger", pendingPolicy: "active-row",
      visible: (row) => Boolean(row.user) && actions.remove.visibleWhen?.(round) === true,
      confirm: { title: () => t("round.action.removeTitle"), body: () => t("round.action.removeBody"), confirm: () => t("round.action.remove") },
      onSelect: async (row) => { await settle(async () => actions.remove.submit!({ responder: row.user }, {
        record: round, selectedIds: [],
      })); },
    })]}
  />;
}

function RoundApproach({ roundId }: { roundId: string }): ReactElement {
  const t = useProposalsT();
  const data = useRoundComparisonData(roundId);
  if (data.error) return <ErrorBanner title={t("comparison.error")} description={data.error.message} />;
  if (data.fetching && !data.round) return <LoadingPanel />;
  if (!data.proposals.length) return <EmptyState title={t("comparison.empty.title")} />;
  return <RoundComparisonGrid topics={data.topics} proposals={data.proposals} answers={data.answers} />;
}
