import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Button, ErrorBanner, Glyph, InlineEmpty, PaneRow, PaneRows, SearchInput, Skeleton, SkeletonStatus, errorMessage } from "@angee/ui";
import { useDebounce } from "use-debounce";
import type { DocumentVariables } from "@angee/refine";

import { useIntegrateVcsT } from "../i18n";
import { IntegrateSearchRepositories, type RepoCandidate } from "../documents";

// Debounce keystrokes before hitting the host search API.
const SEARCH_DEBOUNCE_MS = 250;

type SearchRepositoryVariables = DocumentVariables<typeof IntegrateSearchRepositories>;

export interface RepositoryPickerProps {
  /** The bridge whose host is searched; blank disables the input and search. */
  vcsBridgeId: string;
  /** Called with the candidate a click picked. */
  onPick: (candidate: RepoCandidate) => void;
  /** Names to render as already handled — inventoried, or the current selection. */
  pickedNames?: ReadonlySet<string>;
  /** The label shown against a picked row (e.g. "Added", "Selected"). */
  pickedLabel?: string;
  /** The name whose pick is still in flight. */
  busyName?: string | null;
  id?: string;
  describedBy?: string | undefined;
  readOnly?: boolean;
}

/**
 * Live repository typeahead over one VCS bridge: a debounced search box backed by
 * `search_repositories` (host candidates, not inventoried rows) and a list that
 * reports the picked candidate.
 *
 * The one owner of "find a repository on a host" — it decides nothing about what a
 * pick *means*, so a caller inventorying repositories and a caller selecting one to
 * point a source at share the search, the debounce, and the list chrome while each
 * keeps its own `onPick`. Bridge selection stays with the caller, which already owns
 * a bridge field.
 */
export function RepositoryPicker({
  vcsBridgeId,
  onPick,
  pickedNames,
  pickedLabel,
  busyName = null,
  id,
  describedBy,
  readOnly = false,
}: RepositoryPickerProps): React.ReactElement {
  const t = useIntegrateVcsT();
  const [query, setQuery] = React.useState("");
  const [debouncedQuery] = useDebounce(query.trim(), SEARCH_DEBOUNCE_MS);
  const searchEnabled = vcsBridgeId !== "" && debouncedQuery !== "";
  const searchVars = React.useMemo<SearchRepositoryVariables>(
    () => ({ vcsBridgeId, query: debouncedQuery }),
    [vcsBridgeId, debouncedQuery],
  );
  const searchQuery = useAuthoredQuery(IntegrateSearchRepositories, searchVars, {
    enabled: searchEnabled,
  });
  const candidates = searchQuery.data?.search_repositories ?? [];

  // A bridge change invalidates what was typed against the previous host.
  React.useEffect(() => {
    setQuery("");
  }, [vcsBridgeId]);

  return (
    <div className="flex flex-col gap-3">
      <SearchInput
        id={id}
        aria-label={t("addRepo.nameLabel")}
        aria-describedby={describedBy}
        placeholder={t("addRepo.namePlaceholder")}
        value={query}
        disabled={readOnly || vcsBridgeId === ""}
        onChange={(event) => setQuery(event.currentTarget.value)}
      />
      {searchEnabled && searchQuery.error ? (
        <ErrorBanner
          description={errorMessage(searchQuery.error, t("addRepo.searchFailed"))}
          actions={<Button variant="secondary" size="sm" onClick={() => void searchQuery.refetch()}>{t("addRepo.retry")}</Button>}
        />
      ) : (
        <RepoCandidateList
          candidates={candidates}
          fetching={searchQuery.isFetching}
          searching={searchEnabled}
          hasBridge={vcsBridgeId !== ""}
          busyName={busyName}
          pickedNames={pickedNames}
          pickedLabel={pickedLabel ?? t("addRepo.added")}
          onPick={onPick}
          readOnly={readOnly}
        />
      )}
    </div>
  );
}

function RepoCandidateList({
  candidates,
  fetching,
  searching,
  hasBridge,
  busyName,
  pickedNames,
  pickedLabel,
  onPick,
  readOnly,
}: {
  candidates: readonly RepoCandidate[];
  fetching: boolean;
  searching: boolean;
  hasBridge: boolean;
  busyName: string | null;
  pickedNames: ReadonlySet<string> | undefined;
  pickedLabel: string;
  onPick: (candidate: RepoCandidate) => void;
  readOnly: boolean;
}): React.ReactElement {
  const t = useIntegrateVcsT();
  if (!hasBridge) {
    return <InlineEmpty label={t("addRepo.selectIntegration")} />;
  }
  if (!searching) {
    return <InlineEmpty label={t("addRepo.typeToSearch")} />;
  }
  if (fetching && candidates.length === 0) {
    return <CandidateListSkeleton label={t("addRepo.searching")} />;
  }
  if (candidates.length === 0) {
    return <InlineEmpty label={t("addRepo.noMatches")} />;
  }
  // Each candidate is a pane row whose own control picks it; a picked or busy row
  // keeps its facts but is no longer a control.
  return (
    <PaneRows className="max-h-72 overflow-auto">
      {candidates.map((candidate) => {
        const isPicked = pickedNames?.has(candidate.name) ?? false;
        const isBusy = busyName === candidate.name;
        const pickable = !readOnly && !isPicked && !isBusy;
        return (
          <li key={candidate.name} aria-busy={isBusy || undefined}>
            <PaneRow
              icon="repository"
              title={candidate.name}
              sub={`${candidate.default_branch} · ${candidate.visibility}`}
              selected={isPicked}
              label={pickable ? t("addRepo.pick") : undefined}
              onOpen={pickable ? () => onPick(candidate) : undefined}
              meta={isBusy ? (
                <SkeletonStatus label={t("addRepo.searching")} className="shrink-0">
                  <Skeleton className="h-3 w-12" shape="text" />
                </SkeletonStatus>
              ) : isPicked ? (
                <span className="flex items-center gap-1"><Glyph decorative name="check" />{pickedLabel}</span>
              ) : undefined}
              verb={pickable ? t("addRepo.pick") : undefined}
            />
          </li>
        );
      })}
    </PaneRows>
  );
}

function CandidateListSkeleton({ label }: { label: React.ReactNode }): React.ReactElement {
  // The shape of three pane rows while the host answers.
  return <SkeletonStatus label={label} className="grid gap-1 py-1">
    {Array.from({ length: 3 }, (_, index) => <div key={index} className="flex items-center gap-2 px-1 py-1.5">
      <Skeleton className="size-4 rounded-full" />
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className={index === 1 ? "h-3 w-2/3" : "h-3 w-1/2"} shape="text" />
        <Skeleton className="h-3 w-1/3" shape="text" />
      </div>
    </div>)}
  </SkeletonStatus>;
}
