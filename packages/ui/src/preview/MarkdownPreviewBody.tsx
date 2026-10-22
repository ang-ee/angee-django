import { type ReactElement } from "react";

import { Markdown } from "../widgets/markdown-prose";

/**
 * The markdown preview body: the shared `Markdown` prose over the file's text,
 * code-split so react-markdown loads on first markdown preview rather than at
 * boot. Mounted lazily by {@link MarkdownPreview} inside the preview pane's
 * Suspense boundary.
 */
export default function MarkdownPreviewBody({
  text,
}: {
  text: string;
}): ReactElement {
  return <Markdown value={text} className="h-full overflow-auto bg-sheet p-6" />;
}
