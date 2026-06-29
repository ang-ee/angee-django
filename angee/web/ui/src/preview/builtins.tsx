import { useEffect, useState, type ReactElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { formatSize, isJsonMime } from "./model";
import {
  type PreviewProvider,
  type PreviewProviderProps,
} from "./registry";

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
  const t = useBaseT();
  const { text, loading, error } = useFileText(url);
  if (loading) return <LoadingPanel message={t("preview.loading")} />;
  if (error) return <EmptyState title={t("preview.loadError")} description={error.message} />;
  return children(text);
}

function ImagePreview({ file }: PreviewProviderProps): ReactElement {
  return (
    <div className="grid h-full place-content-center overflow-auto bg-inset p-4">
      <img
        src={file.url}
        alt={file.name}
        className="max-h-full max-w-full rounded-6 object-contain shadow-sm"
      />
    </div>
  );
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
      {(text) => (
        <div
          className={cn(
            "prose-angee h-full overflow-auto bg-sheet p-6 text-fg-2",
            "[&_h1]:mb-2 [&_h1]:text-2xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-lg [&_h2]:font-semibold",
            "[&_p]:my-2 [&_code]:rounded-6 [&_code]:bg-inset [&_code]:px-1 [&_a]:text-link [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-6",
          )}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        </div>
      )}
    </FileText>
  );
}

function FallbackPreview({ file }: PreviewProviderProps): ReactElement {
  const t = useBaseT();
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
