import * as React from "react";
import {
  Button,
  ControlBand,
  Dialog,
  Glyph,
  Input,
  RelationField,
  Spinner,
  useDebounce,
  type RelationOption,
} from "@angee/base";
import {
  useAuthoredMutation,
  useAuthoredQuery,
  useModelInvalidation,
} from "@angee/sdk";

import {
  ADD_REPOSITORY_MUTATION,
  SEARCH_REPOSITORIES_QUERY,
  VCS_INTEGRATIONS_QUERY,
  type AddRepositoryData,
  type AddRepositoryVariables,
  type RepoCandidate,
  type SearchRepositoriesData,
  type SearchRepositoriesVariables,
  type VcsIntegrationsData,
  type VcsIntegrationsVariables,
} from "../documents";

/** The repository model whose list refetches after an add. */
const REPOSITORY_MODEL = "integrate.Repository";
// One safety-capped read of the integration catalogue for the picker.
const INTEGRATION_LIMIT = 200;
// Debounce keystrokes before hitting the host search API.
const SEARCH_DEBOUNCE_MS = 250;

/**
 * The "Add repository" affordance: a control-band button opening a dialog that
 * picks a VCS integration and types a repository name like a foreign-key field.
 * Matches against `searchRepositories` (live host candidates, debounced) and on
 * pick inventories the row via `addRepository`, refreshing the repository list.
 * The dialog stays open so several repositories can be added in one sitting.
 */
export function AddRepositoryControl(): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  return (
    <ControlBand>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        Add repository
      </Button>
      <AddRepositoryDialog open={open} onOpenChange={setOpen} />
    </ControlBand>
  );
}

const INTEGRATION_VARS: VcsIntegrationsVariables = {
  pagination: { offset: 0, limit: INTEGRATION_LIMIT },
};

function AddRepositoryDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const integrationsQuery = useAuthoredQuery<
    VcsIntegrationsData,
    VcsIntegrationsVariables
  >(VCS_INTEGRATIONS_QUERY, INTEGRATION_VARS, { enabled: open });
  const integrationOptions = React.useMemo<readonly RelationOption[]>(
    () =>
      (integrationsQuery.data?.vcsIntegrations.results ?? []).map(
        (integration) => ({
          value: integration.id,
          label: integration.displayName,
        }),
      ),
    [integrationsQuery.data],
  );

  const [pickedId, setPickedId] = React.useState<string | null>(null);
  // Auto-select when the account is unambiguous, so a single-integration host
  // skips straight to typing.
  const soleIntegration =
    integrationOptions.length === 1 ? integrationOptions[0] : undefined;
  const vcsIntegrationId = pickedId ?? soleIntegration?.value ?? "";

  const [query, setQuery] = React.useState("");
  const [debouncedQuery] = useDebounce(query.trim(), SEARCH_DEBOUNCE_MS);
  const searchEnabled = open && vcsIntegrationId !== "" && debouncedQuery !== "";
  const searchVars = React.useMemo<SearchRepositoriesVariables>(
    () => ({ vcsIntegrationId, query: debouncedQuery }),
    [vcsIntegrationId, debouncedQuery],
  );
  const searchQuery = useAuthoredQuery<
    SearchRepositoriesData,
    SearchRepositoriesVariables
  >(SEARCH_REPOSITORIES_QUERY, searchVars, { enabled: searchEnabled });
  const candidates = searchQuery.data?.searchRepositories ?? [];

  const [addRepository] = useAuthoredMutation<
    AddRepositoryData,
    AddRepositoryVariables
  >(ADD_REPOSITORY_MUTATION);
  const refreshRepositories = useModelInvalidation(REPOSITORY_MODEL);

  // The repo currently being inventoried, the set already added this session, and
  // the last error — so a slow host or a denied add reads clearly in the dialog.
  const [adding, setAdding] = React.useState<string | null>(null);
  const [added, setAdded] = React.useState<ReadonlySet<string>>(new Set());
  const [error, setError] = React.useState<string | null>(null);

  // Reset per-session state whenever the dialog closes or the integration changes.
  React.useEffect(() => {
    if (!open) {
      setQuery("");
      setAdded(new Set());
      setError(null);
      setAdding(null);
    }
  }, [open]);
  React.useEffect(() => {
    setAdded(new Set());
    setError(null);
  }, [vcsIntegrationId]);

  const add = React.useCallback(
    async (candidate: RepoCandidate) => {
      if (vcsIntegrationId === "") return;
      setAdding(candidate.name);
      setError(null);
      try {
        await addRepository({ vcsIntegrationId, name: candidate.name });
        setAdded((prev) => new Set(prev).add(candidate.name));
        refreshRepositories();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Could not add repository.");
      } finally {
        setAdding(null);
      }
    },
    [addRepository, refreshRepositories, vcsIntegrationId],
  );

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title>Add repository</Dialog.Title>
                <Dialog.Description>
                  Pick a VCS integration, then type to find a repository to
                  inventory.
                </Dialog.Description>
              </div>
              <Dialog.Close />
            </div>
          </Dialog.Header>
          <Dialog.Body>
            <div className="flex flex-col gap-3">
              <RelationField
                aria-label="VCS integration"
                value={vcsIntegrationId}
                options={integrationOptions}
                placeholder="Select an integration"
                searchPlaceholder="Search integrations…"
                onChange={setPickedId}
              />
              <Input
                type="search"
                aria-label="Repository name"
                placeholder="Type a repository name…"
                value={query}
                disabled={vcsIntegrationId === ""}
                onChange={(event) => setQuery(event.currentTarget.value)}
              />
              {error ? (
                <p className="text-13 text-danger-text" role="alert">
                  {error}
                </p>
              ) : null}
              <RepoCandidateList
                candidates={candidates}
                fetching={searchQuery.fetching}
                searching={searchEnabled}
                hasIntegration={vcsIntegrationId !== ""}
                adding={adding}
                added={added}
                onAdd={add}
              />
            </div>
          </Dialog.Body>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function RepoCandidateList({
  candidates,
  fetching,
  searching,
  hasIntegration,
  adding,
  added,
  onAdd,
}: {
  candidates: readonly RepoCandidate[];
  fetching: boolean;
  searching: boolean;
  hasIntegration: boolean;
  adding: string | null;
  added: ReadonlySet<string>;
  onAdd: (candidate: RepoCandidate) => void;
}): React.ReactElement {
  if (!hasIntegration) {
    return <ListHint>Select an integration to search its repositories.</ListHint>;
  }
  if (!searching) {
    return <ListHint>Type a repository name to search.</ListHint>;
  }
  if (fetching && candidates.length === 0) {
    return (
      <div className="flex items-center gap-2 px-1 py-3 text-13 text-fg-muted">
        <Spinner size="sm" />
        Searching…
      </div>
    );
  }
  if (candidates.length === 0) {
    return <ListHint>No matching repositories.</ListHint>;
  }
  return (
    <ul className="flex max-h-72 flex-col gap-1 overflow-auto">
      {candidates.map((candidate) => {
        const isAdded = added.has(candidate.name);
        const isAdding = adding === candidate.name;
        return (
          <li key={candidate.name}>
            <button
              type="button"
              disabled={isAdded || isAdding}
              onClick={() => onAdd(candidate)}
              className="flex w-full items-center gap-3 rounded-md border border-border bg-sheet px-3 py-2 text-left outline-none transition-colors hover:border-border-strong focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-13 text-fg">{candidate.name}</div>
                <div className="truncate text-12 text-fg-muted">
                  {candidate.defaultBranch} · {candidate.visibility}
                </div>
              </div>
              {isAdding ? (
                <Spinner size="sm" />
              ) : isAdded ? (
                <span className="flex items-center gap-1 text-12 text-fg-muted">
                  <Glyph decorative name="check" />
                  Added
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
  return <p className="px-1 py-3 text-13 text-fg-muted">{children}</p>;
}
