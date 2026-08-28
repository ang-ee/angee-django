import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  DashboardView,
  InlineEmpty,
  Metric,
  MiniCard,
  RailPanel,
  RelativeTime,
} from "@angee/ui";

import { NexusOverview } from "./documents";
import { useNexusT } from "./i18n";

const PEEK_LIMIT = 6;

/** Nexus-owned relationship health contributed into the Parties overview seam. */
export function NexusOverviewContribution(): React.ReactElement {
  const t = useNexusT();
  const query = useAuthoredQuery(
    NexusOverview,
    { peekLimit: PEEK_LIMIT },
    { models: ["nexus.Tie", "nexus.Cadence", "parties.Party"] },
  );
  const overview = query.data?.nexus_overview;
  const fading = overview?.fading_ties ?? [];
  const due = overview?.due_cadences ?? [];
  return (
    <DashboardView>
      <Metric
        label={t("overview.fading.metric")}
        value={count(overview?.fading_count, query.fetching)}
        icon="radar"
        tone="warning"
      />
      <Metric
        label={t("overview.due.metric")}
        value={count(overview?.due_count, query.fetching)}
        icon="cadence"
        tone="warning"
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <RailPanel title={t("overview.fading.title")} count={overview?.fading_count ?? 0} fetching={query.fetching}>
          {fading.length > 0 ? (
            <div className="grid gap-2">
              {fading.map((tie) => (
                <MiniCard
                  key={tie.id}
                  title={`${tie.party_a.display_name} ↔ ${tie.party_b.display_name}`}
                  meta={
                    tie.last_interaction_at
                      ? <RelativeTime value={tie.last_interaction_at} />
                      : undefined
                  }
                  primaryTag={{ label: tie.gravity.toFixed(2), tone: "warning" }}
                />
              ))}
            </div>
          ) : (
            <InlineEmpty label={t("overview.fading.empty")} />
          )}
        </RailPanel>
        <RailPanel title={t("overview.due.title")} count={overview?.due_count ?? 0} fetching={query.fetching}>
          {due.length > 0 ? (
            <div className="grid gap-2">
              {due.map((cadence) => (
                <MiniCard
                  key={cadence.id}
                  title={cadence.party.display_name}
                  meta={cadence.touch_due_at ? <RelativeTime value={cadence.touch_due_at} /> : undefined}
                  primaryTag={{
                    label: t("graph.cadenceDays", { count: cadence.cadence_days }),
                    tone: "warning",
                  }}
                />
              ))}
            </div>
          ) : (
            <InlineEmpty label={t("overview.due.empty")} />
          )}
        </RailPanel>
      </div>
    </DashboardView>
  );
}

function count(value: number | undefined, fetching: boolean): string {
  return value === undefined && fetching ? "—" : (value ?? 0).toLocaleString();
}
