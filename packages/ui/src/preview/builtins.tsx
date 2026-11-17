import { lazy, useEffect, useState, type ReactElement } from "react";

import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";
import { ScrollArea } from "../ui/scroll-area";
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

/** Fetch a preview file and read its response for the fetching renderers. */
function usePreviewFileContent<T>(
  url: string,
  read: (response: Response) => Promise<T>,
): PreviewFileState<T> {
  const [state, setState] = useState<PreviewFileState<T>>({ status: "loading" });

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`Preview fetch failed (${response.status})`);
        return read(response);
      })
      .then((content) => live && setState({ status: "ready", content }))
      .catch(
        (error: unknown) =>
          live &&
          setState({
            status: "error",
            error: error instanceof Error ? error : new Error("Preview failed"),
          }),
      );
    return () => {
      live = false;
    };
  }, [url, read]);

  return state;
}

export interface PreviewFileContentProps<T> {
  url: string;
  /**
   * Reads the fetched response: its text, its bytes, or a parse of either. Keep it
   * stable (a module-level function); a new function refetches. A rejection shows
   * the load-error surface.
   */
  read: (response: Response) => Promise<T>;
  children: (content: T) => ReactElement;
}

/**
 * Fetch a preview file and own its loading and error surfaces, so a renderer only
 * describes its happy path: it receives what `read` resolved via the render prop.
 * @owner PreviewPane
 */
export function PreviewFileContent<T>({ url, read, children }: PreviewFileContentProps<T>): ReactElement {
  const t = useUiT();
  const state = usePreviewFileContent(url, read);
  if (state.status === "loading") return <PreviewSkeleton label={t("preview.loading")} variant="text" />;
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
