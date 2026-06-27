import { useMemo, type ReactElement } from "react";

import {
  Button,
  EmptyState,
  Glyph,
  Spinner,
  cn,
  formatDate as formatBaseDate,
  textRoleVariants,
  useResolvedWidget,
  type WidgetField,
} from "@angee/ui";
import { useKnowledgeT } from "../i18n";
import type { KnowledgePageDetail } from "../data/documents";
import { usePageEditor, type SaveStatus } from "../data/use-page-editor";

/** Bound translator for the knowledge namespace. */
type Translate = ReturnType<typeof useKnowledgeT>;

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
  const t = useKnowledgeT();
  const editor = usePageEditor(
    detail.id,
    {
      title: detail.title,
      body: detail.markdown?.body ?? "",
      bodyHash: detail.markdown?.body_hash ?? "",
    },
    onSaved,
  );
  const markdown = useResolvedWidget("markdown.editor");
  const Body = markdown?.edit;
  const isNote = detail.kind !== "folder";
  const bodyField = useMemo<WidgetField>(
    () => ({ name: "body", label: t("knowledge.editor.bodyPlaceholder") }),
    [t],
  );

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
            placeholder={t("knowledge.editor.titlePlaceholder")}
            aria-label={t("knowledge.editor.titleLabel")}
            className="min-w-0 flex-1 border-0 bg-transparent text-28 font-semibold leading-9 text-fg outline-none placeholder:text-fg-subtle"
            onChange={(event) => editor.setTitle(event.currentTarget.value)}
            onBlur={editor.commitTitle}
          />
          <Button
            type="button"
            size="iconMd"
            variant="ghost"
            aria-label={t("knowledge.editor.deleteLabel")}
            onClick={onDelete}
          >
            <Glyph name="trash" />
          </Button>
        </div>
        <div className={cn(textRoleVariants({ role: "meta" }), "flex items-center gap-2 pl-6 font-mono")}>
          <span>{metaLine(detail, t)}</span>
          <SaveBadge status={editor.status} t={t} />
        </div>
      </header>

      {isNote && Body ? (
        <Body
          value={editor.body}
          field={bodyField}
          onChange={(next) => editor.setBody(typeof next === "string" ? next : "")}
        />
      ) : (
        <EmptyState
          fill
          icon="folder"
          title={t("knowledge.editor.folderTitle")}
          description={t("knowledge.editor.folderDescription")}
        />
      )}
    </div>
  );
}

function SaveBadge({
  status,
  t,
}: {
  status: SaveStatus;
  t: Translate;
}): ReactElement | null {
  if (status === "idle") return null;
  if (status === "saving") {
    return (
      <span className="inline-flex items-center gap-1 text-fg-muted">
        <Spinner size="sm" />
        {t("knowledge.editor.saving")}
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="text-danger-text">{t("knowledge.editor.saveFailed")}</span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-success-text">
      <Glyph decorative name="check" />
      {t("knowledge.editor.saved")}
    </span>
  );
}

function metaLine(detail: KnowledgePageDetail, t: Translate): string {
  const parts = [
    detail.created_by_label ?? "—",
    formatBaseDate(detail.updated_at) || "—",
  ];
  if (detail.markdown) {
    parts.push(
      t("knowledge.editor.wordCount", {
        count: detail.markdown.word_count.toLocaleString(),
      }),
    );
  }
  return parts.join(" · ");
}
