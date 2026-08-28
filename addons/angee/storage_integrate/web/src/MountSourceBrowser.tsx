import {
  type DocumentData,
  type DocumentVariables,
  useAuthoredQuery,
} from "@angee/refine";
import {
  Button,
  ErrorBanner,
  Glyph,
  Input,
  Spinner,
  cn,
  errorMessage,
  mutationDialogValueCodecs,
  textRoleVariants,
  type MutationDialogControlProps,
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

export interface MountSourceBrowserProps extends MutationDialogControlProps {
  backendClass: string;
}

/** Bare MutationDialog control for an opaque source-root token. */
export function MountSourceBrowser({
  backendClass,
  id,
  readOnly,
  describedBy,
  labelledBy,
  value,
  onChange,
}: MountSourceBrowserProps): React.ReactElement {
  const t = useStorageIntegrateT();
  const currentValue = mutationDialogValueCodecs.string(value) ?? "";
  const [token, setToken] = React.useState(currentValue);
  const [manualToken, setManualToken] = React.useState(currentValue);

  React.useEffect(() => {
    const timer = window.setTimeout(() => {
      setToken(manualToken.trim());
    }, BROWSE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [manualToken]);

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
      setManualToken(nextToken);
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
        <Input
          id={id}
          aria-labelledby={labelledBy}
          placeholder={t("mount.browse.manualHint")}
          value={manualToken}
          disabled={readOnly}
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
              disabled={readOnly || !entry.is_navigable}
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
