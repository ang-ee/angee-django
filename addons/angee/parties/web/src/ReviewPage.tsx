import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Avatar,
  Button,
  EmptyState,
  ErrorBanner,
  Glyph,
  ListView,
  LoadingPanel,
  Page,
  PageBody,
  PageHeader,
  RailPanel,
  Tag,
  TextLink,
  avatarInitials,
  type ListColumn,
  type StringIdRow,
  useAuthoredResourceMutation,
} from "@angee/ui";
import { Link } from "@tanstack/react-router";
import {
  ConfirmPartyHandle,
  DismissPartyHandle,
  DuplicatePartyCandidates,
  PARTY_HANDLE_DECISION_INVALIDATES,
  PartyReviewCounts,
} from "./documents";
import { usePartiesT } from "./i18n";
import { partyMergePath } from "./routes";

type SuggestionRow = StringIdRow;

/**
 * Human identity review, split between uncertain party↔handle claims and
 * deterministic duplicate candidates that share a normalized handle.
 */
export function ReviewPage(): React.ReactElement {
  const t = usePartiesT();
  const counts = useAuthoredQuery(PartyReviewCounts, undefined, {
    models: ["parties.PartyHandle"],
  });
  const duplicates = useAuthoredQuery(DuplicatePartyCandidates, { limit: 50 }, {
    models: ["parties.Party", "parties.Handle", "parties.MergeVeto"],
  });
  const [confirm, { fetching: confirming }] = useAuthoredResourceMutation(
    ConfirmPartyHandle,
    { invalidateModels: PARTY_HANDLE_DECISION_INVALIDATES },
  );
  const [dismiss, { fetching: dismissing }] = useAuthoredResourceMutation(
    DismissPartyHandle,
    { invalidateModels: PARTY_HANDLE_DECISION_INVALIDATES },
  );
  const busy = confirming || dismissing;

  const columns = React.useMemo<readonly ListColumn<SuggestionRow>[]>(
    () => [
      { field: "handle.value", header: t("review.handle") },
      { field: "handle.platform", header: t("review.platform") },
      { field: "party.display_name", header: t("review.party") },
      { field: "confidence" },
      { field: "source" },
      {
        field: "id",
        header: t("review.actions"),
        headerVisuallyHidden: true,
        sortable: false,
        align: "right",
        render: (row) => (
          <span className="inline-flex gap-1">
            <Button
              variant="ghost"
              size="iconSm"
              aria-label={t("identity.confirm")}
              title={t("identity.confirm")}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                void confirm({ id: row.id });
              }}
            >
              <Glyph name="check" />
            </Button>
            <Button
              variant="ghost"
              size="iconSm"
              aria-label={t("identity.dismiss")}
              title={t("identity.dismiss")}
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                void dismiss({ id: row.id });
              }}
            >
              <Glyph name="x" />
            </Button>
          </span>
        ),
      },
    ],
    [busy, confirm, dismiss, t],
  );
  const handleCount = counts.data?.party_handles_aggregate.aggregate?.count ?? 0;
  const duplicateCandidates = duplicates.data?.duplicate_party_candidates ?? [];

  return (
    <Page>
      <PageHeader title={t("review.title")} description={t("review.description")} />
      <PageBody>
        <div className="grid gap-5">
          <RailPanel
            title={t("review.handleLinks")}
            count={handleCount}
            fetching={counts.fetching}
          >
            {counts.error ? <ErrorBanner description={t("review.error")} /> : null}
            <ListView<SuggestionRow>
              resource="parties.PartyHandle"
              fields={[
                "id",
                "handle.value",
                "handle.platform",
                "party.display_name",
                "confidence",
                "source",
              ]}
              baseFilter={{
                is_confirmed: { exact: false },
                is_dismissed: { exact: false },
                confidence: { lt: 0.5 },
              }}
              columns={columns}
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
            fetching={duplicates.fetching && duplicateCandidates.length > 0}
          >
            {duplicates.fetching && duplicateCandidates.length === 0 ? (
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
                    <Link to={partyMergePath(candidate.left.id, candidate.right.id)}>
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
