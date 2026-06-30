import * as React from "react";
import {
  Button,
  Dialog,
  FieldLabel,
  FieldRoot,
  Glyph,
  Input,
  RelationField,
  Spinner,
  errorMessage,
  textRoleVariants,
  useAuthoredMutation,
  useAuthoredQuery,
  useRelationOptions,
  useToast,
} from "@angee/ui";

import {
  AddAddonSource,
  AddonSources,
  ScanAddonSource,
  type AddonSourceRow,
} from "../documents";
import { usePlatformT } from "../i18n";
import { ADDON_MODEL } from "./AddonCard";

// The VCS bridge an addon source is inventoried on — a local checkout in dev, or a
// host bridge. Picked like a foreign key (the integrate addon owns the resource; we
// reference it by name through the runtime resource metadata).
const VCS_BRIDGE_RELATION = {
  resource: "integrate.VcsBridge",
  labelField: "display_name",
  canCreate: false,
};

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
        {t("platform.apps.scan")}
      </Button>
      <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
        <Glyph decorative name="plus" />
        {t("platform.apps.addSource")}
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
  const [pickedId, setPickedId] = React.useState<string | null>(null);
  // Auto-select when there is exactly one bridge, so a single-bridge dev host skips
  // straight to typing the repository.
  const soleBridge = bridgeOptions.length === 1 ? bridgeOptions[0] : undefined;
  const vcsBridgeId = pickedId ?? soleBridge?.value ?? "";

  const [name, setName] = React.useState("");
  const [ref, setRef] = React.useState("");
  const [path, setPath] = React.useState("");
  const [addSource, { fetching }] = useAuthoredMutation(AddAddonSource, {
    invalidateModels: [ADDON_MODEL],
    shouldInvalidate: (data) => Boolean(data?.add_source?.ok),
  });

  React.useEffect(() => {
    if (!open) {
      setName("");
      setRef("");
      setPath("");
      setPickedId(null);
    }
  }, [open]);

  const ready = vcsBridgeId !== "" && name.trim() !== "" && !fetching;
  const submit = React.useCallback(async () => {
    if (vcsBridgeId === "" || name.trim() === "") return;
    try {
      const result = (
        await addSource({
          data: {
            vcs_bridge_id: vcsBridgeId,
            name: name.trim(),
            ref: ref.trim(),
            path: path.trim(),
          },
        })
      )?.add_source;
      if (result?.ok) {
        toast.success({ title: result.message });
        onOpenChange(false);
      } else {
        toast.danger({ title: result?.message ?? t("platform.apps.actionFailed") });
      }
    } catch (cause) {
      toast.danger({ title: errorMessage(cause, t("platform.apps.actionFailed")) });
    }
  }, [addSource, vcsBridgeId, name, ref, path, toast, t, onOpenChange]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="md">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title>{t("platform.apps.addSource.title")}</Dialog.Title>
                <Dialog.Description>
                  {t("platform.apps.addSource.description")}
                </Dialog.Description>
              </div>
              <Dialog.Close />
            </div>
          </Dialog.Header>
          <Dialog.Body>
            <div className="flex flex-col gap-3">
              <FieldRoot>
                <FieldLabel nativeLabel={false} render={<span />}>
                  {t("platform.apps.addSource.bridge")}
                </FieldLabel>
                <RelationField
                  aria-label={t("platform.apps.addSource.bridge")}
                  value={vcsBridgeId}
                  options={bridgeOptions}
                  placeholder={t("platform.apps.addSource.bridgePlaceholder")}
                  onChange={setPickedId}
                />
              </FieldRoot>
              <FieldRoot>
                <FieldLabel htmlFor="addon-source-name">
                  {t("platform.apps.addSource.repo")}
                </FieldLabel>
                <Input
                  id="addon-source-name"
                  value={name}
                  placeholder={t("platform.apps.addSource.repoPlaceholder")}
                  disabled={vcsBridgeId === ""}
                  onChange={(event) => setName(event.currentTarget.value)}
                />
              </FieldRoot>
              <FieldRoot>
                <FieldLabel htmlFor="addon-source-ref">
                  {t("platform.apps.addSource.ref")}
                </FieldLabel>
                <Input
                  id="addon-source-ref"
                  value={ref}
                  placeholder={t("platform.apps.addSource.refPlaceholder")}
                  onChange={(event) => setRef(event.currentTarget.value)}
                />
              </FieldRoot>
              <FieldRoot>
                <FieldLabel htmlFor="addon-source-path">
                  {t("platform.apps.addSource.path")}
                </FieldLabel>
                <Input
                  id="addon-source-path"
                  value={path}
                  placeholder={t("platform.apps.addSource.pathPlaceholder")}
                  onChange={(event) => setPath(event.currentTarget.value)}
                />
              </FieldRoot>
            </div>
          </Dialog.Body>
          <Dialog.Footer>
            <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
              {t("platform.apps.cancel")}
            </Button>
            <Button variant="primary" size="sm" disabled={!ready} onClick={submit}>
              {fetching ? t("platform.apps.adding") : t("platform.apps.add")}
            </Button>
          </Dialog.Footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
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
          toast.danger({ title: result?.message ?? t("platform.apps.actionFailed") });
        }
      } catch (cause) {
        toast.danger({ title: errorMessage(cause, t("platform.apps.actionFailed")) });
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
                <Dialog.Title>{t("platform.apps.scan.title")}</Dialog.Title>
                <Dialog.Description>{t("platform.apps.scan.description")}</Dialog.Description>
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
        <Spinner size="sm" /> {t("platform.apps.scan.loading")}
      </div>
    );
  }
  if (sources.length === 0) {
    return <p className={textRoleVariants({ role: "meta" })}>{t("platform.apps.scan.empty")}</p>;
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
              {t("platform.apps.scan")}
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
