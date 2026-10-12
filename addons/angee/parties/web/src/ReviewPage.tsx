import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Avatar, EmptyState, ErrorBanner, Glyph, ListView, LoadingPanel, NavLink, Page, PageBody, PageHeader, RailPanel, Tag, avatarInitials, type ListColumn, type StringIdRow, useRouteHref, useResourceRecordHrefLookup } from "@angee/ui";
import { SameAsPairs, PartyReviewCounts } from "./documents";
import { usePartiesT } from "./i18n";
import { usePartyStatementRowActions } from "./party-statement-row-actions";

type SuggestionRow = StringIdRow & {
  source_kind?: string;
  evidence?: Array<{ model: string; id: string }>;
  evidence_truncated?: boolean;
};

/**
 * Human identity review, split between uncertain party↔handle claims and
 * deterministic same_as statements that share a normalized handle.
 */
export function ReviewPage(): React.ReactElement {
  const t = usePartiesT();
  const routeHref = useRouteHref();
  const recordHref = useResourceRecordHrefLookup();
  const counts = useAuthoredQuery(PartyReviewCounts, undefined, {
    models: ["parties.PartyStatement"],
  });
  const sameAs = useAuthoredQuery(SameAsPairs, { limit: 50 }, {
    models: ["parties.Party", "parties.Handle", "parties.PartySameAsPairStatement"],
  });
  const rowActions = usePartyStatementRowActions<SuggestionRow>("all");

  const columns = React.useMemo<readonly ListColumn<SuggestionRow>[]>(
    () => [
      { field: "handle.value", header: t("review.handle") },
      { field: "handle.platform", header: t("review.platform") },
      { field: "subject.display_name", header: t("review.party") },
      { field: "confidence" },
      { field: "source_kind", header: t("identity.claim") },
      {
        field: "evidence",
        header: t("identity.evidence"),
        render: (row) => <span className="flex flex-wrap gap-2">{row.evidence?.map((ref, index) => {
          const href = recordHref(ref.model, ref.id);
          return href ? <NavLink key={`${ref.model}:${ref.id}`} href={href} variant="inline">
            {t("identity.evidenceSource", { number: index + 1 })}
          </NavLink> : null;
        })}{row.evidence_truncated ? <span>{t("identity.evidenceTruncated")}</span> : null}</span>,
      },
    ],
    [recordHref, t],
  );
  const handleCount = counts.data?.party_statements_aggregate.aggregate?.count ?? 0;
  const sameAsCandidates = sameAs.data?.same_as_pairs ?? [];

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
            <p className="mb-3 text-13 text-fg-muted">{t("identity.authenticationScope")}</p>
            <ListView<SuggestionRow>
              resource="parties.PartyStatement"
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
            count={sameAsCandidates.length}
            fetching={sameAs.isFetching && sameAsCandidates.length > 0}
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
                      <span className="flex flex-wrap items-center gap-2">
                        <Tag tone="warning">{t("review.sameAs.sharedHandle")}</Tag>
                        <span className="truncate font-mono text-xs text-fg-muted">
                          {candidate.normalized_value}
                        </span>
                      </span>
                      <span className="text-xs font-medium text-link">
                        {t("review.sameAs.compare")}
                      </span>
                    </span>
                  </NavLink>
                ))}
              </div>
            )}
          </RailPanel>
        </div>
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
