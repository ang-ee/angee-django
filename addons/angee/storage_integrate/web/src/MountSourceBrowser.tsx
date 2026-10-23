import {
  type DocumentData,
  type DocumentVariables,
  useAuthoredQuery,
} from "@angee/refine";
import {
  Button,
  ErrorBanner,
  Glyph,
  InlineEmpty,
  LoadingPanel,
  PaneRow,
  PaneRows,
  SearchInput,
  cn,
  errorMessage,
  mutationDialogValueCodecs,
  textRoleVariants,
  useDebouncedText,
  type DescriptorFieldControlProps,
} from "@angee/ui";
import * as React from "react";

import { BrowseMountSource } from "./documents";
import { useStorageIntegrateT } from "./i18n";

const BROWSE_DEBOUNCE_MS = 250;

type BrowseVariables = DocumentVariables<typeof BrowseMountSource>;
type BrowseResult = DocumentData<
  typeof BrowseMountSource
>["browse_mount_source"];
type MountLocation = BrowseResult["entries"][number];

export interface MountSourceBrowserProps extends DescriptorFieldControlProps {
  backendClass: string;
}

/** Bare MutationDialog control for an opaque source-root token. */
export function MountSourceBrowser({
  backendClass,
  id,
  readOnly,
  invalid,
  describedBy,
  labelledBy,
  value,
  onChange,
}: MountSourceBrowserProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const currentValue = mutationDialogValueCodecs.string(value) ?? "";
  const [token, setToken] = React.useState(currentValue);
  // The typed location is a draft until the debounce commits it as the browsed token.
  const manual = useDebouncedText(token, (next) => {
    setToken(next.trim());
    onChange("");
  }, BROWSE_DEBOUNCE_MS);

  const variables = React.useMemo<BrowseVariables>(
    () => ({
      backendClass,
      token,
    }),
    [backendClass, token],
  );
  const query = useAuthoredQuery(BrowseMountSource, variables);
  const result = query.data?.browse_mount_source;

  const navigate = React.useCallback(
    (nextToken: string) => {
      setToken(nextToken);
      onChange("");
    },
    [onChange],
  );
  const selected = Boolean(result && currentValue === result.location.token);
  const currentReason = result
    ? displayBlockedReason(result.location, t)
    : "";

  return (
    <div
      role="group"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      aria-invalid={invalid || undefined}
      className="grid gap-3"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-13 text-fg">{result?.location.label ?? ""}</p>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={readOnly || !result?.parent_token}
          onClick={() => navigate(result?.parent_token ?? "")}
        >
          <Glyph decorative name="arrow-up" />
          {t("mount.browse.up")}
        </Button>
      </div>

      {result?.supports_manual_token ? (
        <SearchInput
          id={id}
          aria-labelledby={labelledBy}
          aria-describedby={describedBy}
          invalid={invalid}
          placeholder={t("mount.browse.manualHint")}
          value={manual.draft}
          disabled={readOnly}
          onChange={(event) => { manual.setDraft(event.currentTarget.value); manual.commit(event.currentTarget.value); }}
          onClear={() => { manual.setDraft(""); manual.commit(""); }}
        />
      ) : null}

      <ErrorBanner
        description={
          query.error ? errorMessage(query.error, t("mount.browse.error")) : null
        }
      />
      <LocationList
        entries={result?.entries ?? []}
        fetching={query.isFetching}
        readOnly={readOnly}
        onNavigate={navigate}
      />

      {result?.truncated ? (
        <p className={cn(textRoleVariants({ role: "meta" }), "px-1")}>
          {t("mount.browse.truncated")}
        </p>
      ) : null}
      <div className="flex items-center justify-between gap-3">
        <p className={cn(textRoleVariants({ role: "meta" }), "min-w-0 flex-1")}>
          {result && !result.location.is_mountable ? currentReason : null}
        </p>
        <Button
          type="button"
          variant="primary"
          size="sm"
          active={selected}
          aria-pressed={selected}
          disabled={readOnly || !result?.location.is_mountable}
          onClick={() => {
            if (result?.location.is_mountable) onChange(result.location.token);
          }}
        >
          {selected ? <Glyph decorative name="check" /> : null}
          {t("mount.browse.useThisFolder")}
        </Button>
      </div>
    </div>
  );
}

function LocationList({
  entries,
  fetching,
  readOnly,
  onNavigate,
}: {
  entries: readonly MountLocation[];
  fetching: boolean;
  readOnly: boolean;
  onNavigate: (token: string) => void;
}): React.ReactElement {
  const t = useStorageIntegrateT();
  if (fetching && entries.length === 0) {
    return <LoadingPanel density="inline" message={t("mount.browse.loading")} />;
  }
  if (entries.length === 0) {
    return <InlineEmpty icon="folder" label={t("mount.browse.empty")} />;
  }
  // Each location is a pane row; opening a navigable one browses into it.
  return (
    <PaneRows className="max-h-72 overflow-auto">
      {entries.map((entry) => {
        const reason = displayBlockedReason(entry, t);
        const navigable = !readOnly && entry.is_navigable;
        return (
          <li key={entry.token}>
            <PaneRow icon="folder" title={entry.label} detail={!entry.is_mountable && reason ? reason : undefined}
              label={navigable ? t("mount.browse.open") : undefined}
              verb={navigable ? t("mount.browse.open") : undefined}
              onOpen={navigable ? () => onNavigate(entry.token) : undefined} />
          </li>
        );
      })}
    </PaneRows>
  );
}

function displayBlockedReason(
  location: Pick<MountLocation, "is_navigable" | "blocked_reason">,
  t: (key: string) => string,
): string {
  if (!location.is_navigable) return t("mount.browse.notReadable");
  if (location.blocked_reason === "Already mounted") {
    return t("mount.browse.alreadyMounted");
  }
  return location.blocked_reason;
}
