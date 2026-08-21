import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Button, Dialog, Glyph, MutationDialog, Spinner, errorMessage, mutationDialogValueCodecs, textRoleVariants, useAuthoredResourceMutation, useRelationOptions, useToast, type MutationDialogField, type MutationDialogValues } from "@angee/ui";
import { RepositoryPicker, VCS_BRIDGE_RELATION } from "@angee/integrate";

import {
  AddAddonSource,
  AddonSources,
  PLATFORM_ADDON_MUTATION_INVALIDATES,
  ScanAddonSource,
  type AddonSourceRow,
} from "../documents";
import { usePlatformT } from "../i18n";

/**
 * The marketplace source controls for the board toolbar: **Add source** inventories a
 * repository on a VCS bridge and points a new addon `Source` at it; **Scan** re-runs an
 * existing source's discovery, materialising its `addon.toml` rows into the board. Both
 * are admin-gated server-side; the buttons render for everyone and the mutation refuses.
 */
export function AddonSourceControls(): React.ReactElement {
  const t = usePlatformT();
  const [addOpen, setAddOpen] = React.useState(false);
  const [scanOpen, setScanOpen] = React.useState(false);
  return (
    <div className="flex items-center gap-2">
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
    </div>
  );
}

function AddSourceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const t = usePlatformT();
  const toast = useToast();
  const { options: bridgeOptions } = useRelationOptions(VCS_BRIDGE_RELATION, {
    enabled: open,
    sort: true,
  });
  // Auto-select when there is exactly one bridge, so a single-bridge dev host skips
  // straight to typing the repository.
  const soleBridge = bridgeOptions.length === 1 ? bridgeOptions[0] : undefined;
  const vcsBridgeId = soleBridge?.value ?? "";

  const [addSource] = useAuthoredResourceMutation(AddAddonSource, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
    shouldInvalidate: (data) => Boolean(data?.add_source?.ok),
  });
  const fields = React.useMemo<readonly MutationDialogField[]>(
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
      onSubmit={async (values) => {
        const result = (
          await addSource(values)
        )?.add_source;
        if (result?.ok) {
          toast.success({ title: result.message });
          return;
        }
        throw new Error(result?.message ?? t("apps.actionFailed"));
      }}
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
  const t = usePlatformT();
  const toast = useToast();
  const query = useAuthoredQuery(AddonSources, undefined, { enabled: open });
  const sources = query.data?.sources ?? [];
  const { refetch } = query;
  const [scan] = useAuthoredResourceMutation(ScanAddonSource, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
    shouldInvalidate: (data) => Boolean(data?.scan?.ok),
  });
  const [scanning, setScanning] = React.useState<string | null>(null);

  const runScan = React.useCallback(
    async (id: string) => {
      setScanning(id);
      try {
        const result = (await scan({ sourceId: id }))?.scan;
        if (result?.ok) {
          toast.success({ title: result.message });
          refetch();
        } else {
          toast.danger({ title: result?.message ?? t("apps.actionFailed") });
        }
      } catch (cause) {
        toast.danger({ title: errorMessage(cause, t("apps.actionFailed")) });
      } finally {
        setScanning(null);
      }
    },
    // `refetch` is the stable, memoized query member — depend on it, not the whole
    // result object (which gets a fresh identity every render).
    [scan, toast, t, refetch],
  );

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="md">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title>{t("apps.scan.title")}</Dialog.Title>
                <Dialog.Description>{t("apps.scan.description")}</Dialog.Description>
              </div>
              <Dialog.Close />
            </div>
          </Dialog.Header>
          <Dialog.Body>
            <ScanSourceList
              sources={sources}
              fetching={query.fetching}
              scanning={scanning}
              onScan={runScan}
            />
          </Dialog.Body>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
  const t = usePlatformT();
  if (fetching && sources.length === 0) {
    return (
      <div className={textRoleVariants({ role: "meta" })}>
        <Spinner size="sm" /> {t("apps.scan.loading")}
      </div>
    );
  }
  if (sources.length === 0) {
    return <p className={textRoleVariants({ role: "meta" })}>{t("apps.scan.empty")}</p>;
  }
  return (
    <ul className="flex max-h-72 flex-col gap-1 overflow-auto">
      {sources.map((source) => {
        const scope = [source.ref, source.path].filter(Boolean).join(" · ");
        return (
          <li
            key={source.id}
            className="flex items-center gap-3 rounded-6 border border-border bg-sheet px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-13 text-fg">{source.display_name}</div>
              {scope ? <div className="truncate text-12 text-fg-muted">{scope}</div> : null}
            </div>
            <Button
              variant="secondary"
              size="sm"
              disabled={scanning !== null}
              onClick={() => onScan(source.id)}
            >
              {scanning === source.id ? (
                <Spinner size="sm" />
              ) : (
                <Glyph decorative name="search" />
              )}
              {t("apps.scan")}
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
