import { type ReactElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "../lib/cn";

/**
 * The react-markdown body of the markdown preview, code-split so react-markdown
 * and remark-gfm load on first markdown preview rather than at boot. Mounted
 * lazily by {@link MarkdownPreview} inside the preview pane's Suspense boundary.
 */
export default function MarkdownPreviewBody({
  text,
}: {
  text: string;
}): ReactElement {
  return (
    <div
      className={cn(
        "prose-angee h-full overflow-auto bg-sheet p-6 text-fg-2",
        "[&_h1]:mb-2 [&_h1]:text-2xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-lg [&_h2]:font-semibold",
        "[&_p]:my-2 [&_code]:rounded-6 [&_code]:bg-inset [&_code]:px-1 [&_a]:text-link [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-6",
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
