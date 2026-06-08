import { useEffect, useState, type ReactElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { cn } from "../lib/cn";
import { formatSize, isJsonMime } from "./model";
import {
  registerPreviewProvider,
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

function ImagePreview({ file }: PreviewProviderProps): ReactElement {
  return (
    <div className="grid h-full place-content-center overflow-auto bg-inset p-4">
      <img
        src={file.url}
        alt={file.name}
        className="max-h-full max-w-full rounded-md object-contain shadow-sm"
      />
    </div>
  );
}

function TextPreview({ file, mime }: PreviewProviderProps): ReactElement {
  const { text, loading, error } = useFileText(file.url);
  if (loading) return <LoadingPanel message="Loading preview…" />;
  if (error) return <EmptyState title="Could not load file" description={error.message} />;
  const body = isJsonMime(mime) ? prettyJson(text) : text;
  return (
    <pre className="h-full overflow-auto bg-sheet p-4 font-mono text-13 leading-relaxed text-fg-2">
      {body}
    </pre>
  );
}

function MarkdownPreview({ file }: PreviewProviderProps): ReactElement {
  const { text, loading, error } = useFileText(file.url);
  if (loading) return <LoadingPanel message="Loading preview…" />;
  if (error) return <EmptyState title="Could not load file" description={error.message} />;
  return (
    <div
      className={cn(
        "prose-angee h-full overflow-auto bg-sheet p-6 text-fg-2",
        "[&_h1]:mb-2 [&_h1]:text-2xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-lg [&_h2]:font-semibold",
        "[&_p]:my-2 [&_code]:rounded [&_code]:bg-inset [&_code]:px-1 [&_a]:text-link [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-6",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function FallbackPreview({ file }: PreviewProviderProps): ReactElement {
  return (
    <EmptyState
      icon="files"
      title={file.name}
      description={file.size != null ? formatSize(file.size) : "No inline preview."}
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

let registered = false;

/**
 * Register the lightweight built-in renderers (image, markdown, json, text/
 * code, and a generic fallback) — all on dependencies already in the stack.
 * Idempotent. Heavy renderers (pdf, docx, media, syntax highlighting) register
 * from their own lazy module against the same registry.
 */
export function registerBuiltinPreviewProviders(): void {
  if (registered) return;
  registered = true;
  registerPreviewProvider({ id: "base.image", mime: "image/*", component: ImagePreview });
  registerPreviewProvider({
    id: "base.markdown",
    mime: (mime) => mime === "text/markdown" || mime === "text/x-markdown",
    component: MarkdownPreview,
    priority: 10,
  });
  registerPreviewProvider({
    id: "base.json",
    mime: isJsonMime,
    component: TextPreview,
    priority: 10,
  });
  registerPreviewProvider({
    id: "base.text",
    mime: (mime) => mime.startsWith("text/"),
    component: TextPreview,
  });
  registerPreviewProvider({ id: "base.fallback", mime: "*/*", component: FallbackPreview, priority: -10 });
}
