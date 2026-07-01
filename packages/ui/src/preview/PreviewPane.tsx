import { type ReactElement, type ReactNode } from "react";
import { usePreviews } from "../runtime";

import { EmptyState } from "../fragments/EmptyState";
import { LazyBoundary } from "../fragments/LazyBoundary";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { useBaseT } from "../i18n";
import { builtinPreviewProviders } from "./builtins";
import { displayMime } from "./model";
import {
  resolvePreviewProvider,
  type PreviewFile,
  type PreviewProvider,
} from "./registry";

export interface PreviewPaneProps {
  file: PreviewFile;
  /** Explicit content type; when omitted it is derived from the file. */
  mime?: string | null;
  /** Rendered when no provider resolves or a renderer crashes. */
  fallback?: ReactNode;
}

/**
 * The preview surface: resolves the matching renderer for a file's mime — from
 * the built-ins plus any addon-contributed providers on the runtime — and mounts
 * it inside a Suspense boundary (renderers may lazy-load their deps) and an error
 * boundary (a renderer crash degrades to the fallback, not a blank pane). With no
 * match it renders the fallback.
 */
export function PreviewPane({
  file,
  mime,
  fallback,
}: PreviewPaneProps): ReactElement {
  const t = useBaseT();
  const resolvedMime = mime ?? displayMime(file);
  // Addon-contributed providers first so one can override a built-in at an equal
  // priority (resolve sorts stably); built-ins are always available. The SDK
  // tracks only the contribution id for collision detection — base addons author
  // full `PreviewProvider`s, so this widening cast is a deliberate boundary.
  const runtimePreviews = usePreviews() as readonly PreviewProvider[];
  const provider = resolvePreviewProvider(
    [...runtimePreviews, ...builtinPreviewProviders],
    resolvedMime,
  );
  const empty = fallback ?? <EmptyState title={t("preview.unavailable")} />;
  if (!provider) return <>{empty}</>;

  const Renderer = provider.component;
  // Key the renderer by provider + file so it remounts (not just re-renders)
  // when the previewed file changes — a renderer's per-file state (e.g. a PDF's
  // page index) must not carry across files in a persistent pane.
  const instanceKey = `${provider.id}:${file.url}`;
  return (
    <LazyBoundary
      pending={<LoadingPanel message={t("preview.loading")} />}
      fallback={empty}
      resetKey={instanceKey}
    >
      <Renderer key={instanceKey} file={file} mime={resolvedMime} />
    </LazyBoundary>
  );
}
