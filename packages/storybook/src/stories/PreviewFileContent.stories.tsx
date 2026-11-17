import { PreviewFileContent } from "@angee/ui";

const NOTES = `data:text/plain;charset=utf-8,${encodeURIComponent("Reading circle notes\n\n1. Doors open at eight.\n2. Rows A to C are held for the readers.")}`;

const readText = (response: Response): Promise<string> => response.text();

function Notes({ url }: { url: string }) {
  return (
    <div className="h-64 max-w-xl overflow-hidden rounded-6 border border-border-subtle">
      <PreviewFileContent url={url} read={readText}>
        {(text) => <pre className="h-full overflow-auto whitespace-pre-wrap bg-sheet p-4 font-mono text-13 text-fg-2">{text}</pre>}
      </PreviewFileContent>
    </div>
  );
}

export default { title: "Previews/PreviewFileContent", component: PreviewFileContent, parameters: { layout: "padded" } };

/** The renderer describes only its happy path; the owner fetches, reads and shows loading and errors. */
export const Default = { render: () => <Notes url={NOTES} /> };

/** A failed fetch shows the shared load-error surface. */
export const FetchFailed = { render: () => <Notes url="/missing-preview-file.txt" /> };
