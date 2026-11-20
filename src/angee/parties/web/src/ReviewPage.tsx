import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Alert, Avatar, Button, EmptyState, ErrorBanner, Glyph, ListView, LoadingPanel, NavLink, Page, PageBody, PageHeader, RailPanel, ResourcePaneFooter, Chip, avatarInitials, formatNumber, type ListColumn, useRouteHref } from "@angee/ui";
import { PARTY_MERGE_INVALIDATES, SameAsPairs, PartyReviewCounts } from "./documents";
import { DuplicatesDialog } from "./DuplicatesDialog";
import { usePartiesT } from "./i18n";
import { useIdentityClaimColumns, type IdentityClaimRow } from "./identity-columns";
import { usePartyStatementRowActions } from "./party-statement-row-actions";

type SuggestionRow = IdentityClaimRow & { source_kind?: string };

/**
 * The possible duplicates read a page at a time, and the certain ones a batch at
 * a time: the batch is the server's ``PartyManager.BATCH_SETS`` (copied, as in
 * ``merge-selection.tsx``; no read carries it).
 */
const SAME_AS_PAGE = 50;

/**
 * Human identity review, split between uncertain party↔handle claims and
 * deterministic same_as statements that share a normalized handle. Each list
 * carries its own total (`PartyReviewCounts`) and its own pager: the claims list
 * pages inside its section, and the possible duplicates page after the last pair
 * seen (`same_as_pairs(after:)`), so a pair merged or kept apart meanwhile moves
 * no other one. Every certain pair merges from one dialog (`DuplicatesDialog`'s
 * `allCertain`), which lists them all page by page before one confirm.
 */
export function ReviewPage(): React.ReactElement {
  const t = usePartiesT();
  const routeHref = useRouteHref();
  const shared = useIdentityClaimColumns<SuggestionRow>();
  // While the batch dialog is open its merges would refetch these reads per set; they are held and read once after
  // it closes (the dialog invalidates the models once, when its run ends).
  const [mergingCertain, setMergingCertain] = React.useState(false);
  const held = { enabled: !mergingCertain };
  // The claims and pairs totals change with every statement, merge and keep-apart decision.
  const counts = useAuthoredQuery(PartyReviewCounts, undefined, { models: PARTY_MERGE_INVALIDATES, ...held });
  // The first pair of each page seen so far: the next page continues after the last pair of this one.
  const [pageStarts, setPageStarts] = React.useState<readonly (string | null)[]>([null]);
  const page = pageStarts.length;
  const sameAs = useAuthoredQuery(SameAsPairs, { limit: SAME_AS_PAGE, after: pageStarts[page - 1] ?? null }, {
    models: ["parties.Party", "parties.Handle", "parties.PartySameAsPairStatement"],
    ...held,
  });
  const rowActions = usePartyStatementRowActions<SuggestionRow>("all");
  const handleCount = counts.data?.party_statements_aggregate.aggregate?.count ?? 0;
  const pairTotal = counts.data?.same_as_pair_counts.pairs;
  const certainTotal = counts.data?.same_as_pair_counts.certain ?? 0;
  // The totals are exact up to the server's cap; past it they read "1,000+" and the certain count "<n>+".
  const capped = counts.data?.same_as_pair_counts.capped ?? false;
  const shownTotal = (value: number): string =>
    capped ? t("review.cappedCount", { count: formatNumber(value) }) : formatNumber(value);
  const sameAsCandidates = sameAs.data?.same_as_pairs ?? [];
  // A page whose every pair has left (merged, kept apart) steps back to the page before it.
  React.useEffect(() => {
    if (page > 1 && sameAs.data && !sameAs.isFetching && sameAs.data.same_as_pairs.length === 0) {
      setPageStarts((starts) => starts.slice(0, -1));
    }
  }, [page, sameAs.data, sameAs.isFetching]);

  const columns = React.useMemo<readonly ListColumn<SuggestionRow>[]>(
    () => [
      { field: "handle.value", header: t("review.handle") },
      { field: "handle.platform", header: t("review.platform") },
      { field: "subject.display_name", header: t("review.party") },
      shared.confidence,
      { field: "source_kind", header: t("identity.claim") },
      shared.evidence,
    ],
    [shared, t],
  );

  return (
    <Page>
      <PageHeader
        title={t("review.title")}
        description={t("review.description")}
      />
      <PageBody>
        <div className="grid gap-5">
          <RailPanel
            title={t("review.statements")}
            count={handleCount}
            fetching={counts.isFetching}
          >
            {counts.error ? <ErrorBanner description={t("review.error")} /> : null}
            <Alert tone="info" className="mb-3">{t("identity.authenticationScope")}</Alert>
            <ListView<SuggestionRow>
              resource="parties.PartyStatement"
              // Embedded: its pager pages this list inside its own section, not the page's control row.
              presentation="embedded"
              // Statements are reviewed per row; no action consumes a selection.
              selectable={false}
              fields={[
                "id",
                "handle.value",
                "handle.platform",
                "subject.display_name",
                "confidence",
                "source_kind",
                "evidence.model",
                "evidence.id",
                "evidence_truncated",
              ]}
              baseFilter={{
                to_review: { exact: true },
              }}
              columns={columns}
              rowActions={rowActions}
              emptyContent={{
                icon: "user-check",
                title: t("review.statements.empty.title"),
                description: t("review.statements.empty.description"),
              }}
            />
          </RailPanel>

          <RailPanel
            title={t("review.sameAs")}
            count={pairTotal === undefined ? undefined : shownTotal(pairTotal)}
            fetching={sameAs.isFetching && sameAsCandidates.length > 0}
            actions={certainTotal > 0 ? (
              <Button type="button" size="sm" variant="secondary" onClick={() => setMergingCertain(true)}>
                {t("review.sameAs.mergeCertain", { total: shownTotal(certainTotal) })}
              </Button>
            ) : undefined}
          >
            {sameAs.isFetching && sameAsCandidates.length === 0 ? (
              <LoadingPanel density="inline" />
            ) : sameAs.error ? (
              <ErrorBanner description={t("review.error")} />
            ) : sameAsCandidates.length === 0 ? (
              <EmptyState
                icon="users"
                title={t("review.sameAs.empty.title")}
                description={t("review.sameAs.empty.description")}
              />
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {sameAsCandidates.map((candidate) => (
                  <NavLink
                    key={`${candidate.left.id}:${candidate.right.id}`}
                    variant="block-card"
                    href={routeHref("parties.merge", {
                      left: candidate.left.id,
                      right: candidate.right.id,
                    })}
                  >
                    <span className="grid gap-3">
                      <span className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                        <PartySummary party={candidate.left} />
                        <Glyph name="chevron-right" className="text-fg-muted" />
                        <PartySummary party={candidate.right} align="right" />
                      </span>
                      {/* The card is the link to the comparison; the shared handle is the reason. */}
                      <span className="flex flex-wrap items-center gap-2">
                        <Chip tone="warning">{t("review.sameAs.sharedHandle")}</Chip>
                        <Chip mono className="min-w-0">{candidate.normalized_value}</Chip>
                      </span>
                    </span>
                  </NavLink>
                ))}
              </div>
            )}
            {page > 1 || (pairTotal ?? 0) > SAME_AS_PAGE ? (
              <ResourcePaneFooter
                pager={{
                  page,
                  pageSize: SAME_AS_PAGE,
                  total: capped ? undefined : pairTotal,
                  hasPrev: page > 1,
                  hasNext: sameAsCandidates.length === SAME_AS_PAGE
                    && (pairTotal === undefined || capped || page * SAME_AS_PAGE < pairTotal),
                }}
                disabled={sameAs.isFetching}
                pagerTotalUnit={t("review.sameAs.pagerUnit")}
                labelElement="span"
                onPageChange={(next) => {
                  const last = sameAsCandidates[sameAsCandidates.length - 1]?.id;
                  setPageStarts((starts) => (next > starts.length && last ? [...starts, last] : starts.slice(0, Math.max(1, next))));
                }}
              />
            ) : null}
          </RailPanel>
        </div>
        {mergingCertain ? (
          <DuplicatesDialog
            allCertain={{ total: shownTotal(certainTotal) }}
            onClose={() => setMergingCertain(false)}
          />
        ) : null}
      </PageBody>
    </Page>
  );
}

function PartySummary({
  party,
  align = "left",
}: {
  party: { display_name: string; display_avatar?: { url?: string | null } | null };
  align?: "left" | "right";
}): React.ReactElement {
  return (
    <span className={`flex min-w-0 items-center gap-2 ${align === "right" ? "flex-row-reverse text-right" : ""}`}>
      <Avatar size="sm" src={party.display_avatar?.url || undefined} initials={avatarInitials(party.display_name)} />
      <span className="truncate text-13 font-semibold text-fg">{party.display_name}</span>
    </span>
  );
}
