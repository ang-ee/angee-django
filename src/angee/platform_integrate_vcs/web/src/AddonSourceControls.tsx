import type { ActionFieldName } from "@angee/gql/console/actions";
import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Button, DialogForm, ErrorBanner, Glyph, InlineEmpty, LoadingPanel, MutationDialog, PaneRow, PaneRows, actionOutcomeSubmitResult, errorMessage, mutationDialogValueCodecs, useActionOutcomeMutation, useActionResultRun, useRelationOptions, type DescriptorField, type MutationDialogValues } from "@angee/ui";
import { RepositoryPicker, VCS_BRIDGE_RELATION } from "@angee/integrate-vcs";
import { PLATFORM_ADDON_MUTATION_INVALIDATES } from "@angee/platform";

import {
  AddonSources,
  type AddonSourceRow,
} from "./documents";
import { usePlatformIntegrateVcsT } from "./i18n";

/**
 * The marketplace source controls for the board toolbar: **Add source** inventories a
 * repository on a VCS bridge and points a new addon `Source` at it; **Scan** re-runs an
 * existing source's discovery, materialising its `pyproject.toml` rows into the board. Both
 * are admin-gated server-side; the buttons render for everyone and the mutation refuses.
 */
export function AddonSourceControls(): React.ReactElement {
  const t = usePlatformIntegrateVcsT();
  const [addOpen, setAddOpen] = React.useState(false);
  const [scanOpen, setScanOpen] = React.useState(false);
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setScanOpen(true)}>
        <Glyph decorative name="search" />
        {t("apps.scan")}
      </Button>
      <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
        <Glyph decorative name="plus" />
        {t("apps.addSource")}
      </Button>
      <AddSourceDialog open={addOpen} onOpenChange={setAddOpen} />
      <ScanSourcesDialog open={scanOpen} onOpenChange={setScanOpen} />
    </>
  );
}

function AddSourceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const t = usePlatformIntegrateVcsT();
  const { options: bridgeOptions } = useRelationOptions(VCS_BRIDGE_RELATION, {
    enabled: open,
    sort: true,
  });
  // Auto-select when there is exactly one bridge, so a single-bridge dev host skips
  // straight to typing the repository.
  const soleBridge = bridgeOptions.length === 1 ? bridgeOptions[0] : undefined;
  const vcsBridgeId = soleBridge?.value ?? "";

  const [addSource] = useActionOutcomeMutation<ActionFieldName>("add_source", {
    idArgument: null,
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
  });
  const fields = React.useMemo<readonly DescriptorField[]>(
    () => [
      {
        name: "vcsBridgeId",
        label: t("apps.addSource.bridge"),
        widget: "many2one",
        options: bridgeOptions,
        placeholder: t("apps.addSource.bridgePlaceholder"),
        required: true,
      },
      {
        name: "name",
        label: t("apps.addSource.repo"),
        placeholder: t("apps.addSource.repoPlaceholder"),
        required: true,
        // Repositories are host candidates, not rows — integrate owns that search,
        // so the picker comes from there rather than being retyped here as a bare
        // `owner/repo` text field the user has to already know.
        control: ({ id, value, readOnly, describedBy, onChange, dialogValues }) => {
          const bridgeId = mutationDialogValueCodecs.string(
            dialogValues.vcsBridgeId,
          );
          const selectedName = mutationDialogValueCodecs.string(value);
          return (
            <RepositoryPicker
              id={id}
              describedBy={describedBy}
              readOnly={readOnly}
              vcsBridgeId={bridgeId ?? ""}
              onPick={(candidate) => onChange(candidate.name)}
              pickedNames={selectedName ? new Set([selectedName]) : undefined}
              pickedLabel={t("apps.addSource.selected")}
            />
          );
        },
      },
      {
        name: "ref",
        label: t("apps.addSource.ref"),
        placeholder: t("apps.addSource.refPlaceholder"),
      },
      {
        name: "path",
        label: t("apps.addSource.path"),
        placeholder: t("apps.addSource.pathPlaceholder"),
      },
    ],
    [bridgeOptions, t],
  );

  return (
    <MutationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("apps.addSource.title")}
      description={t("apps.addSource.description")}
      fields={fields}
      initialValues={{ vcsBridgeId }}
      submitLabel={t("apps.add")}
      submittingLabel={t("apps.adding")}
      errorFallback={t("apps.actionFailed")}
      parseValues={parseAddonSourceValues}
      onSubmit={async (values) => actionOutcomeSubmitResult(await addSource("", values))}
    />
  );
}

export function parseAddonSourceValues(values: MutationDialogValues) {
  const ref = mutationDialogValueCodecs.string(values.ref);
  const path = mutationDialogValueCodecs.string(values.path);
  return {
    data: {
      vcs_bridge_id: mutationDialogValueCodecs.requiredString(
        values.vcsBridgeId,
        "vcsBridgeId",
      ),
      name: mutationDialogValueCodecs.requiredString(values.name, "name"),
      ...(ref === null ? {} : { ref }),
      ...(path === null ? {} : { path }),
    },
  };
}

function ScanSourcesDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const t = usePlatformIntegrateVcsT();
  const query = useAuthoredQuery(AddonSources, undefined, { enabled: open });
  const sources = query.data?.sources ?? [];
  const { refetch } = query;
  const [scan] = useActionOutcomeMutation<ActionFieldName>("scan", {
    idArgument: "source_id",
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
  });
  const settle = useActionResultRun({ noResultTitle: t("apps.actionFailed") });
  const [scanning, setScanning] = React.useState<string | null>(null);

  const runScan = React.useCallback(
    async (id: string) => {
      setScanning(id);
      try {
        const result = await settle(() => scan(id));
        if (result?.ok) void refetch();
      } finally {
        setScanning(null);
      }
    },
    // `refetch` is the stable, memoized query member — depend on it, not the whole
    // result object (which gets a fresh identity every render).
    [scan, settle, refetch],
  );

  // A list of verbs, not a form: Enter never submits anything here.
  return (
    <DialogForm open={open} onOpenChange={onOpenChange} size="md" title={t("apps.scan.title")} description={t("apps.scan.description")}
      onSubmit={(event) => event.preventDefault()}>
      {query.error ? (
        <ErrorBanner
          description={errorMessage(query.error, t("apps.scan.loadError"))}
          actions={<Button variant="secondary" size="sm" onClick={() => void refetch()}>{t("apps.scan.retry")}</Button>}
        />
      ) : (
        <ScanSourceList
          sources={sources}
          fetching={query.isFetching}
          scanning={scanning}
          onScan={runScan}
        />
      )}
    </DialogForm>
  );
}

function ScanSourceList({
  sources,
  fetching,
  scanning,
  onScan,
}: {
  sources: readonly AddonSourceRow[];
  fetching: boolean;
  scanning: string | null;
  onScan: (id: string) => void;
}): React.ReactElement {
  const t = usePlatformIntegrateVcsT();
  if (fetching && sources.length === 0) {
    return <LoadingPanel density="inline" message={t("apps.scan.loading")} />;
  }
  if (sources.length === 0) {
    return <InlineEmpty label={t("apps.scan.empty")} />;
  }
  return (
    <PaneRows className="max-h-72 overflow-auto">
      {sources.map((source) => {
        const scope = [source.ref, source.path].filter(Boolean).join(" · ");
        return (
          <li key={source.id}>
            <PaneRow
              icon="versions"
              title={source.display_name}
              sub={scope || undefined}
              verb={(
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={scanning !== null}
                  loading={scanning === source.id}
                  loadingText={t("apps.scan")}
                  onClick={() => onScan(source.id)}
                >
                  <Glyph decorative name="search" />
                  {t("apps.scan")}
                </Button>
              )}
            />
          </li>
        );
      })}
    </PaneRows>
  );
}
