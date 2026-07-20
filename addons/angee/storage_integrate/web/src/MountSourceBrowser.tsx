import {
  type DocumentData,
  type DocumentVariables,
  useAuthoredQuery,
} from "@angee/refine";
import {
  Button,
  ErrorBanner,
  FieldLabel,
  FieldRoot,
  Glyph,
  Input,
  Spinner,
  cn,
  errorMessage,
  textRoleVariants,
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

export interface MountSourceBrowserProps {
  backendClass: string;
  credentialId?: string | null;
  value: string;
  onChange: (token: string) => void;
  open: boolean;
}

/** Browse and select an opaque source-root token through a registered Mount backend. */
export function MountSourceBrowser({
  backendClass,
  credentialId = null,
  value,
  onChange,
  open,
}: MountSourceBrowserProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const [token, setToken] = React.useState(value || "");
  const [manualToken, setManualToken] = React.useState(value || "");

  React.useEffect(() => {
    const timer = window.setTimeout(() => {
      setToken(manualToken.trim());
    }, BROWSE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [manualToken]);

  React.useEffect(() => {
    if (!open) {
      const resetToken = value || "";
      setToken(resetToken);
      setManualToken(resetToken);
    }
  }, [open, value]);

  const variables = React.useMemo<BrowseVariables>(
    () => ({
      backendClass,
      credentialId,
      token,
    }),
    [backendClass, credentialId, token],
  );
  const query = useAuthoredQuery(BrowseMountSource, variables, { enabled: open });
  const result = query.data?.browse_mount_source;

  const navigate = React.useCallback(
    (nextToken: string) => {
      setToken(nextToken);
      setManualToken(nextToken);
      onChange("");
    },
    [onChange],
  );
  const selected = Boolean(result && value === result.location.token);
  const currentReason = result
    ? displayBlockedReason(result.location, t)
    : "";

  return (
    <FieldRoot>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <FieldLabel>{t("mount.browse.currentFolder")}</FieldLabel>
          <p className="truncate text-13 text-fg">{result?.location.label ?? ""}</p>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={!result?.parent_token}
          onClick={() => navigate(result?.parent_token ?? "")}
        >
          <Glyph decorative name="arrow-up" />
          {t("mount.browse.up")}
        </Button>
      </div>

      {result?.supports_manual_token ? (
        <Input
          aria-label={t("mount.browse.currentFolder")}
          placeholder={t("mount.browse.manualHint")}
          value={manualToken}
          onChange={(event) => {
            setManualToken(event.currentTarget.value);
            onChange("");
          }}
        />
      ) : null}

      <ErrorBanner
        description={
          query.error ? errorMessage(query.error, t("mount.browse.error")) : null
        }
      />
      <LocationList
        entries={result?.entries ?? []}
        fetching={query.fetching}
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
          disabled={!result?.location.is_mountable}
          onClick={() => {
            if (result?.location.is_mountable) onChange(result.location.token);
          }}
        >
          {selected ? <Glyph decorative name="check" /> : null}
          {t("mount.browse.useThisFolder")}
        </Button>
      </div>
    </FieldRoot>
  );
}

function LocationList({
  entries,
  fetching,
  onNavigate,
}: {
  entries: readonly MountLocation[];
  fetching: boolean;
  onNavigate: (token: string) => void;
}): React.ReactElement {
  const t = useStorageIntegrateT();
  if (fetching && entries.length === 0) {
    return (
      <div
        className={cn(
          textRoleVariants({ role: "meta" }),
          "flex items-center gap-2 px-1 py-3",
        )}
      >
        <Spinner size="sm" />
        {t("mount.browse.loading")}
      </div>
    );
  }
  if (entries.length === 0) {
    return <ListHint>{t("mount.browse.empty")}</ListHint>;
  }
  return (
    <ul className="flex max-h-72 flex-col gap-1 overflow-auto">
      {entries.map((entry) => {
        const reason = displayBlockedReason(entry, t);
        return (
          <li key={entry.token}>
            <button
              type="button"
              disabled={!entry.is_navigable}
              onClick={() => onNavigate(entry.token)}
              className="flex w-full items-center gap-3 rounded-6 border border-border bg-sheet px-3 py-2 text-left outline-none transition-colors hover:border-border-strong focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Glyph decorative name="folder" className="shrink-0 text-fg-muted" />
              <span className="min-w-0 flex-1 truncate text-13 text-fg">
                {entry.label}
              </span>
              {!entry.is_mountable && reason ? (
                <span className="max-w-64 truncate text-12 text-fg-muted">
                  {reason}
                </span>
              ) : null}
              <Glyph
                decorative
                name="chevron-right"
                className="shrink-0 text-fg-muted"
              />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ListHint({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <p className={cn(textRoleVariants({ role: "meta" }), "px-1 py-3")}>
      {children}
    </p>
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
