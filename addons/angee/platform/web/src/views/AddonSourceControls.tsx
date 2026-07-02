import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Button, Dialog, Glyph, MutationDialog, Spinner, errorMessage, textRoleVariants, useRelationOptions, useToast, type MutationDialogField } from "@angee/ui";
import { VCS_BRIDGE_RELATION } from "@angee/integrate";

import {
  AddAddonSource,
  AddonSources,
  ScanAddonSource,
  type AddonSourceRow,
} from "../documents";
import { usePlatformT } from "../i18n";
import { ADDON_MODEL } from "./AddonCard";

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

  const [addSource] = useAuthoredMutation(AddAddonSource, {
    invalidateModels: [ADDON_MODEL],
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
        readOnlyWhen: (values) => stringValue(values.vcsBridgeId) === "",
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
      cancelLabel={t("apps.cancel")}
      errorFallback={t("apps.actionFailed")}
      onSubmit={async (values) => {
        const result = (
          await addSource({
            data: {
              vcs_bridge_id: stringValue(values.vcsBridgeId),
              name: stringValue(values.name).trim(),
              ref: stringValue(values.ref).trim(),
              path: stringValue(values.path).trim(),
            },
          })
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

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
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
  const [scan] = useAuthoredMutation(ScanAddonSource, {
    invalidateModels: [ADDON_MODEL],
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
