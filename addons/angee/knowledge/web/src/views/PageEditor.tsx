import { type ReactElement } from "react";

import {
  Button,
  EmptyState,
  Glyph,
  Spinner,
  useResolvedWidget,
  type WidgetField,
} from "@angee/base";

import type { KnowledgePageDetail } from "../data/documents";
import { usePageEditor, type SaveStatus } from "../data/use-page-editor";

const BODY_FIELD: WidgetField = { name: "body", label: "Write your page…" };

export interface PageEditorProps {
  detail: KnowledgePageDetail;
  /** A write landed — refetch the navigator (a rename retitles its tree node). */
  onSaved: () => void;
  /** Delete this page (the page confirms first). */
  onDelete: () => void;
}

/**
 * One page as an editor: an inline title (autosaved on blur through `updatePage`)
 * and the markdown body in the design-system CodeMirror widget, autosaved through
 * `updatePageBody` with its stale-hash guard. Folder pages carry no body.
 */
export function PageEditor({
  detail,
  onSaved,
  onDelete,
}: PageEditorProps): ReactElement {
  const editor = usePageEditor(
    detail.id,
    {
      title: detail.title,
      body: detail.markdown?.body ?? "",
      bodyHash: detail.markdown?.bodyHash ?? "",
    },
    onSaved,
  );
  const markdown = useResolvedWidget("markdown.editor");
  const Body = markdown?.edit;
  const isNote = detail.kind !== "folder";

  return (
    <div className="mx-auto flex h-full w-full max-w-[820px] flex-col gap-4 overflow-auto px-8 py-8">
      <header className="grid gap-1">
        <div className="flex items-center gap-2">
          <Glyph
            decorative
            name={detail.kind === "folder" ? "folder" : "note"}
            className="shrink-0 text-fg-muted"
          />
          <input
            value={editor.title}
            placeholder="Untitled"
            aria-label="Page title"
            className="min-w-0 flex-1 border-0 bg-transparent text-28 font-semibold leading-9 text-fg outline-none placeholder:text-fg-subtle"
            onChange={(event) => editor.setTitle(event.currentTarget.value)}
            onBlur={editor.commitTitle}
          />
          <Button
            type="button"
            size="iconMd"
            variant="ghost"
            aria-label="Delete page"
            onClick={onDelete}
          >
            <Glyph name="trash" />
          </Button>
        </div>
        <div className="flex items-center gap-2 pl-6 font-mono text-13 text-fg-muted">
          <span>{metaLine(detail)}</span>
          <SaveBadge status={editor.status} />
        </div>
      </header>

      {isNote && Body ? (
        <Body
          value={editor.body}
          field={BODY_FIELD}
          onChange={(next) => editor.setBody(typeof next === "string" ? next : "")}
        />
      ) : (
        <div className="grid flex-1 place-content-center">
          <EmptyState
            icon="folder"
            title="Folder"
            description="A folder groups pages — open a note in the tree to edit it."
          />
        </div>
      )}
    </div>
  );
}

function SaveBadge({ status }: { status: SaveStatus }): ReactElement | null {
  if (status === "idle") return null;
  if (status === "saving") {
    return (
      <span className="inline-flex items-center gap-1 text-fg-muted">
        <Spinner size="sm" />
        Saving…
      </span>
    );
  }
  if (status === "error") {
    return <span className="text-danger-text">Save failed</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 text-success-text">
      <Glyph decorative name="check" />
      Saved
    </span>
  );
}

function metaLine(detail: KnowledgePageDetail): string {
  const parts = [detail.createdByLabel ?? "—", formatDate(detail.updatedAt)];
  if (detail.markdown) {
    parts.push(`${detail.markdown.wordCount.toLocaleString()} words`);
  }
  return parts.join(" · ");
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
