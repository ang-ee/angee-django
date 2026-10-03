import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Avatar, EmptyState, ErrorBanner, Glyph, ListView, LoadingPanel, Page, PageBody, PageHeader, RailPanel, Tag, TextLink, avatarInitials, type ListColumn, type StringIdRow, useRouteHref, useResourceRecordHrefLookup } from "@angee/ui";
import { Link } from "@tanstack/react-router";
import { DuplicatePartyCandidates, PartyReviewCounts } from "./documents";
import { usePartiesT } from "./i18n";
import { usePartyHandleRowActions } from "./party-handle-row-actions";

type SuggestionRow = StringIdRow & {
  source?: string;
  evidence_refs?: Array<{ model: string; id: string }>;
  evidence_truncated?: boolean;
};

/**
 * Human identity review, split between uncertain party↔handle claims and
 * deterministic duplicate candidates that share a normalized handle.
 */
export function ReviewPage(): React.ReactElement {
  const t = usePartiesT();
  const routeHref = useRouteHref();
  const recordHref = useResourceRecordHrefLookup();
  const counts = useAuthoredQuery(PartyReviewCounts, undefined, {
    models: ["parties.PartyHandle"],
  });
  const duplicates = useAuthoredQuery(DuplicatePartyCandidates, { limit: 50 }, {
    models: ["parties.Party", "parties.Handle", "parties.MergeVeto"],
  });
  const rowActions = usePartyHandleRowActions<SuggestionRow>("all");

  const columns = React.useMemo<readonly ListColumn<SuggestionRow>[]>(
    () => [
      { field: "handle.value", header: t("review.handle") },
      { field: "handle.platform", header: t("review.platform") },
      { field: "party.display_name", header: t("review.party") },
      { field: "confidence" },
      {
        field: "source",
        header: t("identity.claim"),
        render: (row) => row.source === "EMAIL_MATCH"
          ? t("identity.senderClaim")
          : String(row.source ?? ""),
      },
      {
        field: "evidence_refs",
        header: t("identity.evidence"),
        render: (row) => <span className="flex flex-wrap gap-2">{row.evidence_refs?.map((ref, index) => {
          const href = recordHref(ref.model, ref.id);
          return href ? <TextLink key={`${ref.model}:${ref.id}`} href={href}>
            {t("identity.evidenceSource", { number: index + 1 })}
          </TextLink> : null;
        })}{row.evidence_truncated ? <span>{t("identity.evidenceTruncated")}</span> : null}</span>,
      },
    ],
    [recordHref, t],
  );
  const handleCount = counts.data?.party_handles_aggregate.aggregate?.count ?? 0;
  const duplicateCandidates = duplicates.data?.duplicate_party_candidates ?? [];

  return (
    <Page>
      <PageHeader
        title={t("review.title")}
        description={t("review.description")}
      />
      <PageBody>
        <div className="grid gap-5">
          <RailPanel
            title={t("review.handleLinks")}
            count={handleCount}
            fetching={counts.isFetching}
          >
            {counts.error ? <ErrorBanner description={t("review.error")} /> : null}
            <p className="mb-3 text-13 text-fg-muted">{t("identity.authenticationScope")}</p>
            <ListView<SuggestionRow>
              resource="parties.PartyHandle"
              fields={[
                "id",
                "handle.value",
                "handle.platform",
                "party.display_name",
                "confidence",
                "source",
                "evidence_refs.model",
                "evidence_refs.id",
                "evidence_truncated",
              ]}
              baseFilter={{
                is_confirmed: { exact: false },
                is_dismissed: { exact: false },
                confidence: { lt: 0.5 },
              }}
              columns={columns}
              rowActions={rowActions}
              emptyContent={{
                icon: "user-check",
                title: t("review.handleLinks.empty.title"),
                description: t("review.handleLinks.empty.description"),
              }}
            />
          </RailPanel>

          <RailPanel
            title={t("review.possibleDuplicates")}
            count={duplicateCandidates.length}
            fetching={duplicates.isFetching && duplicateCandidates.length > 0}
          >
            {duplicates.isFetching && duplicateCandidates.length === 0 ? (
              <LoadingPanel density="inline" />
            ) : duplicates.error ? (
              <ErrorBanner description={t("review.error")} />
            ) : duplicateCandidates.length === 0 ? (
              <EmptyState
                icon="users"
                title={t("review.possibleDuplicates.empty.title")}
                description={t("review.possibleDuplicates.empty.description")}
              />
            ) : (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {duplicateCandidates.map((candidate) => (
                  <TextLink key={`${candidate.left.id}:${candidate.right.id}`} asChild variant="block-card">
                    <Link
                      to={routeHref("parties.merge", {
                        left: candidate.left.id,
                        right: candidate.right.id,
                      })}
                    >
                      <span className="grid gap-3">
                        <span className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                          <PartySummary name={candidate.left.display_name} />
                          <Glyph name="chevron-right" className="text-fg-muted" />
                          <PartySummary name={candidate.right.display_name} align="right" />
                        </span>
                        <span className="flex flex-wrap items-center gap-2">
                          <Tag tone="warning">{t("review.possibleDuplicates.sharedHandle")}</Tag>
                          <span className="truncate font-mono text-xs text-fg-muted">
                            {candidate.normalized_value}
                          </span>
                        </span>
                        <span className="text-xs font-medium text-link">
                          {t("review.possibleDuplicates.compare")}
                        </span>
                      </span>
                    </Link>
                  </TextLink>
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
  name,
  align = "left",
}: {
  name: string;
  align?: "left" | "right";
}): React.ReactElement {
  return (
    <span className={`flex min-w-0 items-center gap-2 ${align === "right" ? "flex-row-reverse text-right" : ""}`}>
      <Avatar size="sm" initials={avatarInitials(name)} />
      <span className="truncate text-13 font-semibold text-fg">{name}</span>
    </span>
  );
}
