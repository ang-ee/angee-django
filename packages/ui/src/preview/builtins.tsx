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

/** Fetch a text file's body for the text-based renderers. */
function useFileText(url: string): {
  text: string;
  loading: boolean;
  error: Error | null;
} {
  const [state, setState] = useState<{
    text: string;
    loading: boolean;
    error: Error | null;
  }>({ text: "", loading: true, error: null });

  useEffect(() => {
    let live = true;
    setState({ text: "", loading: true, error: null });
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`Preview fetch failed (${response.status})`);
        return response.text();
      })
      .then((text) => live && setState({ text, loading: false, error: null }))
      .catch(
        (error: unknown) =>
          live &&
          setState({
            text: "",
            loading: false,
            error: error instanceof Error ? error : new Error("Preview failed"),
          }),
      );
    return () => {
      live = false;
    };
  }, [url]);

  return state;
}

/**
 * Fetch a text file and own its loading/error surfaces, so a text-based renderer
 * only describes its happy path: it receives the resolved body via the render prop.
 */
function FileText({
  url,
  children,
}: {
  url: string;
  children: (text: string) => ReactElement;
}): ReactElement {
  const t = useUiT();
  const { text, loading, error } = useFileText(url);
  if (loading) return <PreviewSkeleton label={t("preview.loading")} variant="text" />;
  if (error) return <EmptyState title={t("preview.loadError")} description={error.message} />;
  return children(text);
}

export interface ImagePreviewSurfaceProps {
  src: string;
  alt: string;
}

/**
 * The shared scrollable image surface. Its content owns centering, so when an
 * image is taller than the viewport the content grows and scrolling still
 * begins at the image's top edge instead of centering that edge above the
 * reachable scroll range.
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
    <FileText url={file.url}>
      {(text) => (
        <pre className="h-full overflow-auto bg-sheet p-4 font-mono text-13 leading-relaxed text-fg-2">
          {isJsonMime(mime) ? prettyJson(text) : text}
        </pre>
      )}
    </FileText>
  );
}

function MarkdownPreview({ file }: PreviewProviderProps): ReactElement {
  return (
    <FileText url={file.url}>
      {(text) => <MarkdownPreviewBody text={text} />}
    </FileText>
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
