import * as React from "react";
import {
  Button,
  Dialog,
  Glyph,
  Input,
  RelationField,
  Spinner,
  errorMessage,
  useRelationOptions,
  useAuthoredMutation,
  useAuthoredQuery,
} from "@angee/ui";
import { useDebounce } from "use-debounce";
import type { DocumentVariables } from "@angee/refine";

import { useIntegrateT } from "../i18n";
import {
  IntegrateAddRepository,
  IntegrateSearchRepositories,
  type RepoCandidate,
} from "../documents";

/** The repository model whose list refetches after an add. */
const REPOSITORY_MODEL = "integrate.Repository";
const VCS_BRIDGE_MODEL = "integrate.VcsBridge";
const VCS_BRIDGE_RELATION = {
  resource: VCS_BRIDGE_MODEL,
  labelField: "display_name",
  canCreate: false,
};
// Debounce keystrokes before hitting the host search API.
const SEARCH_DEBOUNCE_MS = 250;

/**
 * The "Add repository" affordance: a button (for the list toolbar slot) opening a
 * dialog that picks a VCS bridge and types a repository name like a foreign-key
 * field. Matches against `search_repositories` (live host candidates, debounced) and
 * on pick inventories the row via `add_repository`, refreshing the repository list.
 * The dialog stays open so several repositories can be added in one sitting.
 */
export function AddRepositoryControl(): React.ReactElement {
  const t = useIntegrateT();
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("integrate.addRepo.title")}
      </Button>
      <AddRepositoryDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

type SearchRepositoryVariables = DocumentVariables<typeof IntegrateSearchRepositories>;

function AddRepositoryDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const t = useIntegrateT();
  const { options: bridgeOptions } = useRelationOptions(VCS_BRIDGE_RELATION, {
    enabled: open,
    sort: true,
  });

  const [pickedId, setPickedId] = React.useState<string | null>(null);
  // Auto-select when the account is unambiguous, so a single-bridge host
  // skips straight to typing.
  const soleBridge = bridgeOptions.length === 1 ? bridgeOptions[0] : undefined;
  const vcsBridgeId = pickedId ?? soleBridge?.value ?? "";

  const [query, setQuery] = React.useState("");
  const [debouncedQuery] = useDebounce(query.trim(), SEARCH_DEBOUNCE_MS);
  const searchEnabled = open && vcsBridgeId !== "" && debouncedQuery !== "";
  const searchVars = React.useMemo<SearchRepositoryVariables>(
    () => ({ vcsBridgeId, query: debouncedQuery }),
    [vcsBridgeId, debouncedQuery],
  );
  const searchQuery = useAuthoredQuery(IntegrateSearchRepositories, searchVars, {
    enabled: searchEnabled,
  });
  const candidates = searchQuery.data?.search_repositories ?? [];

  const [addRepository] = useAuthoredMutation(IntegrateAddRepository, {
    invalidateModels: [REPOSITORY_MODEL],
  });

  // The repo currently being inventoried, the set already added this session, and
  // the last error — so a slow host or a denied add reads clearly in the dialog.
  const [adding, setAdding] = React.useState<string | null>(null);
  const [added, setAdded] = React.useState<ReadonlySet<string>>(new Set());
  const [error, setError] = React.useState<string | null>(null);

  // Reset per-session state whenever the dialog closes or the bridge changes.
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
  }, [vcsBridgeId]);

  const add = React.useCallback(
    async (candidate: RepoCandidate) => {
      if (vcsBridgeId === "") return;
      setAdding(candidate.name);
      setError(null);
      try {
        await addRepository({ vcsBridgeId, name: candidate.name });
        setAdded((prev) => new Set(prev).add(candidate.name));
      } catch (cause) {
        setError(errorMessage(cause, t("integrate.addRepo.addFailed")));
      } finally {
        setAdding(null);
      }
    },
    [addRepository, t, vcsBridgeId],
  );

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title>{t("integrate.addRepo.title")}</Dialog.Title>
                <Dialog.Description>
                  {t("integrate.addRepo.description")}
                </Dialog.Description>
              </div>
              <Dialog.Close />
            </div>
          </Dialog.Header>
          <Dialog.Body>
            <div className="flex flex-col gap-3">
              <RelationField
                aria-label={t("integrate.addRepo.integrationLabel")}
                value={vcsBridgeId}
                options={bridgeOptions}
                placeholder={t("integrate.addRepo.integrationPlaceholder")}
                searchPlaceholder={t("integrate.addRepo.integrationSearch")}
                onChange={setPickedId}
              />
              <Input
                type="search"
                aria-label={t("integrate.addRepo.nameLabel")}
                placeholder={t("integrate.addRepo.namePlaceholder")}
                value={query}
                disabled={vcsBridgeId === ""}
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
                hasBridge={vcsBridgeId !== ""}
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
  hasBridge,
  adding,
  added,
  onAdd,
}: {
  candidates: readonly RepoCandidate[];
  fetching: boolean;
  searching: boolean;
  hasBridge: boolean;
  adding: string | null;
  added: ReadonlySet<string>;
  onAdd: (candidate: RepoCandidate) => void;
}): React.ReactElement {
  const t = useIntegrateT();
  if (!hasBridge) {
    return <ListHint>{t("integrate.addRepo.selectIntegration")}</ListHint>;
  }
  if (!searching) {
    return <ListHint>{t("integrate.addRepo.typeToSearch")}</ListHint>;
  }
  if (fetching && candidates.length === 0) {
    return (
      <div className="flex items-center gap-2 px-1 py-3 text-13 text-fg-muted">
        <Spinner size="sm" />
        {t("integrate.addRepo.searching")}
      </div>
    );
  }
  if (candidates.length === 0) {
    return <ListHint>{t("integrate.addRepo.noMatches")}</ListHint>;
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
                  {candidate.default_branch} · {candidate.visibility}
                </div>
              </div>
              {isAdding ? (
                <Spinner size="sm" />
              ) : isAdded ? (
                <span className="flex items-center gap-1 text-12 text-fg-muted">
                  <Glyph decorative name="check" />
                  {t("integrate.addRepo.added")}
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
