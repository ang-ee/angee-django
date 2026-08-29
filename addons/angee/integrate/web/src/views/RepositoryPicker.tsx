import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { Glyph, Input, Spinner, cn, textRoleVariants } from "@angee/ui";
import { useDebounce } from "use-debounce";
import type { DocumentVariables } from "@angee/refine";

import { useIntegrateT } from "../i18n";
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
  /** The name whose pick is still in flight, rendered with a spinner. */
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
  const t = useIntegrateT();
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
      <Input
        id={id}
        type="search"
        aria-label={t("addRepo.nameLabel")}
        aria-describedby={describedBy}
        placeholder={t("addRepo.namePlaceholder")}
        value={query}
        disabled={readOnly || vcsBridgeId === ""}
        onChange={(event) => setQuery(event.currentTarget.value)}
      />
      <RepoCandidateList
        candidates={candidates}
        fetching={searchQuery.fetching}
        searching={searchEnabled}
        hasBridge={vcsBridgeId !== ""}
        busyName={busyName}
        pickedNames={pickedNames}
        pickedLabel={pickedLabel ?? t("addRepo.added")}
        onPick={onPick}
      />
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
}: {
  candidates: readonly RepoCandidate[];
  fetching: boolean;
  searching: boolean;
  hasBridge: boolean;
  busyName: string | null;
  pickedNames: ReadonlySet<string> | undefined;
  pickedLabel: string;
  onPick: (candidate: RepoCandidate) => void;
}): React.ReactElement {
  const t = useIntegrateT();
  if (!hasBridge) {
    return <ListHint>{t("addRepo.selectIntegration")}</ListHint>;
  }
  if (!searching) {
    return <ListHint>{t("addRepo.typeToSearch")}</ListHint>;
  }
  if (fetching && candidates.length === 0) {
    return (
      <div className={cn(textRoleVariants({ role: "meta" }), "flex items-center gap-2 px-1 py-3")}>
        <Spinner size="sm" />
        {t("addRepo.searching")}
      </div>
    );
  }
  if (candidates.length === 0) {
    return <ListHint>{t("addRepo.noMatches")}</ListHint>;
  }
  return (
    <ul className="flex max-h-72 flex-col gap-1 overflow-auto">
      {candidates.map((candidate) => {
        const isPicked = pickedNames?.has(candidate.name) ?? false;
        const isBusy = busyName === candidate.name;
        return (
          <li key={candidate.name}>
            <button
              type="button"
              disabled={isPicked || isBusy}
              onClick={() => onPick(candidate)}
              className="flex w-full items-center gap-3 rounded-6 border border-border bg-sheet px-3 py-2 text-left outline-none transition-colors hover:border-border-strong focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-13 text-fg">{candidate.name}</div>
                <div className="truncate text-12 text-fg-muted">
                  {candidate.default_branch} · {candidate.visibility}
                </div>
              </div>
              {isBusy ? (
                <Spinner size="sm" />
              ) : isPicked ? (
                <span className="flex items-center gap-1 text-12 text-fg-muted">
                  <Glyph decorative name="check" />
                  {pickedLabel}
                </span>
              ) : (
                <Glyph decorative name="plus" className="text-fg-muted" />
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ListHint({ children }: { children: React.ReactNode }): React.ReactElement {
  return <p className={cn(textRoleVariants({ role: "meta" }), "px-1 py-3")}>{children}</p>;
}
