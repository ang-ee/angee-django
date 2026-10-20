import { useMemo, type ReactElement } from "react";

import {
  Button, ControlBand, EmptyState, Glyph, Skeleton, SkeletonStatus, SurfaceHeader, cn,
  formatDate as formatBaseDate, textRoleVariants, useResolvedWidget, type WidgetField,
} from "@angee/ui";
import { useKnowledgeT } from "../i18n";
import type { KnowledgePageDetail } from "../data/documents";
import { usePageEditor, type SaveStatus } from "../data/use-page-editor";

/** Bound translator for the knowledge namespace. */
type Translate = ReturnType<typeof useKnowledgeT>;

export interface PageEditorProps {
  detail: KnowledgePageDetail;
  /** Delete this page (the page confirms first). */
  onDelete?: () => void;
  onDone: () => void;
}

/**
 * One page as an editor: an inline title (autosaved on blur through `updatePage`)
 * and the markdown body in the design-system CodeMirror widget, autosaved through
 * `updatePageBody` with its stale-hash guard. Folder pages carry no body.
 */
export function PageEditor({
  detail,
  onDelete,
  onDone,
}: PageEditorProps): ReactElement {
  const t = useKnowledgeT();
  const editor = usePageEditor(
    detail.id,
    {
      title: detail.title,
      body: detail.markdown?.body ?? "",
      bodyHash: detail.markdown?.body_hash ?? "",
    },
  );
  const markdown = useResolvedWidget("markdown.editor");
  const Body = markdown?.edit;
  const isNote = detail.kind !== "folder";
  const bodyField = useMemo<WidgetField>(
    () => ({ name: "body", label: t("editor.bodyPlaceholder") }),
    [t],
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      <ControlBand>
        <Button type="button" size="sm" variant="secondary" onClick={onDone}>
          <Glyph name="check" />
          {t("editor.done")}
        </Button>
        {onDelete ? (
          <Button type="button" size="iconSm" variant="ghost" aria-label={t("editor.deleteLabel")} onClick={onDelete}>
            <Glyph name="trash" />
          </Button>
        ) : null}
        <div className="ml-auto text-12 text-fg-muted"><SaveBadge status={editor.status} t={t} /></div>
      </ControlBand>
      <SurfaceHeader density="compact" headingLevel={null} icon={detail.kind === "folder" ? "folder" : "note"}
        title={<input autoFocus value={editor.title} placeholder={t("editor.titlePlaceholder")} aria-label={t("editor.titleLabel")}
          className={cn(textRoleVariants({ role: "title" }), "min-w-0 w-full border-0 bg-transparent outline-none placeholder:text-fg-subtle")}
          onChange={(event) => editor.setTitle(event.currentTarget.value)} onBlur={editor.commitTitle} />}
        subtitle={metaLine(detail, t)} />
      <div className="mx-auto min-h-0 w-full max-w-[820px] flex-1 overflow-auto p-6">
        {isNote && Body ? (
          <Body
            value={editor.body}
            field={bodyField}
            onChange={(next) => editor.setBody(typeof next === "string" ? next : "")}
          />
        ) : (
          <EmptyState fill icon="folder" title={t("editor.folderTitle")} description={t("editor.folderDescription")} />
        )}
      </div>
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
      <SkeletonStatus label={t("editor.saving")} className="inline-flex min-w-16 items-center">
        <Skeleton className="h-3 w-16" shape="text" />
      </SkeletonStatus>
    );
  }
  if (status === "error") {
    return (
      <span className="text-danger-text">{t("editor.saveFailed")}</span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-success-text">
      <Glyph decorative name="check" />
      {t("editor.saved")}
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
      t("editor.wordCount", {
        count: detail.markdown.word_count.toLocaleString(),
      }),
    );
  }
  return parts.join(" · ");
}
