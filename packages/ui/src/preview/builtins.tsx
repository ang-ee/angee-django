import { lazy, useEffect, useState, type ReactElement } from "react";

import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";
import { ScrollArea } from "../ui/scroll-area";
import { useLatestRef } from "../lib/use-latest-ref";
import { formatSize, isJsonMime } from "./model";
import { PreviewSkeleton } from "./PreviewSkeleton";
import {
  type PreviewProvider,
  type PreviewProviderProps,
} from "./registry";

// react-markdown (and remark-gfm) loads on first markdown preview, not at boot.
const MarkdownPreviewBody = lazy(() => import("./MarkdownPreviewBody"));

type PreviewFileState<T> =
  | { status: "loading" }
  | { status: "ready"; content: T }
  | { status: "error"; error: Error };

/** What a preview read gets beside the response. */
export interface PreviewFileReadContext {
  /** Aborted when the file's URL changes or the renderer unmounts; `fetch` and `read` share it. */
  signal: AbortSignal;
  /**
   * Register a release for what the read created, such as an object URL. It runs when
   * the content is replaced or unmounted, or at once when the read was already aborted.
   */
  onCleanup: (cleanup: () => void) => void;
}

/** Fetch a preview file and read its response; refetches only when the URL changes. */
function usePreviewFileContent<T>(
  url: string,
  read: (response: Response, context: PreviewFileReadContext) => Promise<T>,
  fetchFailed: (status: number) => string,
): PreviewFileState<T> {
  // `read` and the copy are read at fetch time, so an inline function never refetches.
  const latest = useLatestRef({ read, fetchFailed });
  const [state, setState] = useState<PreviewFileState<T>>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const cleanups: (() => void)[] = [];
    const onCleanup = (cleanup: () => void) => {
      if (signal.aborted) cleanup();
      else cleanups.push(cleanup);
    };
    setState({ status: "loading" });
    fetch(url, { signal })
      .then((response) => {
        if (!response.ok) throw new Error(latest.current.fetchFailed(response.status));
        return latest.current.read(response, { signal, onCleanup });
      })
      .then((content) => {
        if (!signal.aborted) setState({ status: "ready", content });
      })
      .catch((error: unknown) => {
        if (signal.aborted) return;
        setState({ status: "error", error: error instanceof Error ? error : new Error(String(error)) });
      });
    return () => {
      controller.abort();
      for (const cleanup of cleanups.splice(0)) cleanup();
    };
  }, [url, latest]);

  return state;
}

export interface PreviewFileContentProps<T> {
  url: string;
  /**
   * Reads the fetched response: its text, its bytes, or a parse of either. It may be
   * an inline function; only a new `url` refetches. A rejection shows the load-error
   * surface.
   */
  read: (response: Response, context: PreviewFileReadContext) => Promise<T>;
  /** The pending surface's accessible label; defaults to the shared loading copy. */
  loadingLabel?: string;
  children: (content: T) => ReactElement;
}

/**
 * Fetch a preview file and own its loading, error, abort and release, so a renderer
 * only describes its happy path: it receives what `read` resolved via the render prop.
 * @owner PreviewPane
 */
export function PreviewFileContent<T>({ url, read, loadingLabel, children }: PreviewFileContentProps<T>): ReactElement {
  const t = useUiT();
  const state = usePreviewFileContent(url, read, (status) => t("preview.fetchFailed", { status: String(status) }));
  if (state.status === "loading") return <PreviewSkeleton label={loadingLabel ?? t("preview.loading")} variant="text" />;
  if (state.status === "error") return <EmptyState title={t("preview.loadError")} description={state.error.message} />;
  return children(state.content);
}

const readText = (response: Response): Promise<string> => response.text();

export interface ImagePreviewSurfaceProps {
  src: string;
  alt: string;
}

/**
 * The shared scrollable image surface. Its content owns centering, so when an
 * image is taller than the viewport the content grows and scrolling still
 * begins at the image's top edge instead of centering that edge above the
 * reachable scroll range.
 * @kind surface
 */
export function ImagePreviewSurface({
  src,
  alt,
}: ImagePreviewSurfaceProps): ReactElement {
  return (
    <ScrollArea
      className="h-full bg-inset"
      contentClassName="grid min-h-full place-items-center p-4"
      contentStyle={{ minWidth: "100%", width: "100%" }}
      scrollbars="both"
    >
      <img
        src={src}
        alt={alt}
        className="block max-w-full rounded-6 object-contain shadow-sm"
      />
    </ScrollArea>
  );
}

function ImagePreview({ file }: PreviewProviderProps): ReactElement {
  return <ImagePreviewSurface src={file.url} alt={file.name} />;
}

function TextPreview({ file, mime }: PreviewProviderProps): ReactElement {
  return (
    <PreviewFileContent url={file.url} read={readText}>
      {(text) => (
        <pre className="h-full whitespace-pre-wrap break-words overflow-auto bg-sheet p-4 font-mono text-13 leading-relaxed text-fg-2">
          {isJsonMime(mime) ? prettyJson(text) : text}
        </pre>
      )}
    </PreviewFileContent>
  );
}

function MarkdownPreview({ file }: PreviewProviderProps): ReactElement {
  return (
    <PreviewFileContent url={file.url} read={readText}>
      {(text) => <MarkdownPreviewBody text={text} />}
    </PreviewFileContent>
  );
}

function FallbackPreview({ file }: PreviewProviderProps): ReactElement {
  const t = useUiT();
  return (
    <EmptyState
      icon="files"
      title={file.name}
      description={file.size != null ? formatSize(file.size) : t("preview.noInline")}
    />
  );
}

function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/**
 * The lightweight built-in renderers (image, markdown, json, text/code, and a
 * generic fallback) — all on dependencies already in the stack. `PreviewPane`
 * always resolves against these (plus any addon-contributed providers from the
 * runtime), so they need no registration. An addon adds more renderers — or
 * overrides one of these at a higher priority — through its manifest `previews`.
 * @kind contribution
 */
export const builtinPreviewProviders: readonly PreviewProvider[] = [
  { id: "base.image", mime: "image/*", component: ImagePreview },
  {
    id: "base.markdown",
    mime: (mime) => mime === "text/markdown" || mime === "text/x-markdown",
    component: MarkdownPreview,
    priority: 10,
  },
  { id: "base.json", mime: isJsonMime, component: TextPreview, priority: 10 },
  { id: "base.text", mime: (mime) => mime.startsWith("text/"), component: TextPreview },
  { id: "base.fallback", mime: "*/*", component: FallbackPreview, priority: -10 },
];
