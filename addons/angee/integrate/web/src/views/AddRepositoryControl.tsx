import * as React from "react";
import { Button, DialogForm, Glyph, RelationField, errorMessage, useAuthoredResourceMutation, useRelationOptions } from "@angee/ui";

import { useIntegrateT } from "../i18n";
import { VCS_BRIDGE_RELATION } from "../data/vcs-bridge";
import {
  INTEGRATE_ADD_REPOSITORY_INVALIDATES,
  IntegrateAddRepository,
  type RepoCandidate,
} from "../documents";
import { RepositoryPicker } from "./RepositoryPicker";

/**
 * The "Add repository" affordance: a button (for the list toolbar slot) opening a
 * dialog that picks a VCS bridge and searches its host through the shared
 * {@link RepositoryPicker}. A picked candidate is inventoried via `add_repository`,
 * refreshing the repository list. The dialog stays open so several repositories can
 * be added in one sitting.
 */
export function AddRepositoryControl(): React.ReactElement {
  const t = useIntegrateT();
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("addRepo.title")}
      </Button>
      <AddRepositoryDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

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

  const [addRepository] = useAuthoredResourceMutation(IntegrateAddRepository, {
    invalidateModels: INTEGRATE_ADD_REPOSITORY_INVALIDATES,
  });

  // The repo currently being inventoried, the set already added this session, and
  // the last error — so a slow host or a denied add reads clearly in the dialog.
  const [adding, setAdding] = React.useState<string | null>(null);
  const [added, setAdded] = React.useState<ReadonlySet<string>>(new Set());
  const [error, setError] = React.useState<string | null>(null);

  // Reset per-session state whenever the dialog closes or the bridge changes.
  React.useEffect(() => {
    if (!open) {
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
        setError(errorMessage(cause, t("addRepo.addFailed")));
      } finally {
        setAdding(null);
      }
    },
    [addRepository, t, vcsBridgeId],
  );

  return (
    <DialogForm
      open={open}
      onOpenChange={onOpenChange}
      title={t("addRepo.title")}
      description={t("addRepo.description")}
      size="lg"
      onSubmit={(event) => event.preventDefault()}
    >
      <div className="flex flex-col gap-3">
        <RelationField
          aria-label={t("addRepo.integrationLabel")}
          value={vcsBridgeId}
          options={bridgeOptions}
          placeholder={t("addRepo.integrationPlaceholder")}
          searchPlaceholder={t("addRepo.integrationSearch")}
          onChange={setPickedId}
        />
        {error ? (
          <p className="text-13 text-danger-text" role="alert">
            {error}
          </p>
        ) : null}
        <RepositoryPicker
          vcsBridgeId={vcsBridgeId}
          onPick={(candidate) => void add(candidate)}
          pickedNames={added}
          pickedLabel={t("addRepo.added")}
          busyName={adding}
        />
      </div>
    </DialogForm>
  );
}
