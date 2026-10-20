import { Button, EmptyState, Glyph, PaneRow, PaneRows, Skeleton, SkeletonStatus, formatDate, useResolvedWidget, type WidgetField } from "@angee/ui";
import { useMemo, type ReactElement } from "react";

import type { KnowledgePageDetail, KnowledgePageRow } from "../data/documents";
import { pageIcon } from "../data/page-rows";
import type { PageEditorState, SaveStatus } from "../data/use-page-editor";
import { useKnowledgeT } from "../i18n";

/** The reading body: a note renders its markdown; a folder lists its pages. */
export function PageBody({ detail, pages, onOpenPage, onNewPageHere, onEdit }: {
  detail: KnowledgePageDetail;
  pages: readonly KnowledgePageRow[];
  onOpenPage?: (id: string) => void;
  onNewPageHere?: () => void;
  onEdit?: () => void;
}): ReactElement {
  const t = useKnowledgeT();
  const markdown = useResolvedWidget("markdown.preview");
  const Preview = markdown?.read;
  const body = detail.markdown?.body ?? "";
  if (detail.kind === "folder") {
    return (
      <div className="mx-auto min-h-0 w-full max-w-prose flex-1 overflow-auto p-4">
        {pages.length ? (
          <PaneRows>
            {pages.map((page) => (
              <PaneRow key={page.id} icon={pageIcon(page.kind)} title={page.title}
                meta={formatDate(page.updated_at)} sub={page.created_by_label ?? undefined}
                onOpen={onOpenPage ? () => onOpenPage(page.id) : undefined} />
            ))}
          </PaneRows>
        ) : (
          <EmptyState icon="folder" title={t("folder.emptyTitle")} description={t("folder.emptyDescription")} />
        )}
        {onNewPageHere ? (
          <Button type="button" size="sm" variant="ghost" className="mt-2" onClick={onNewPageHere}>
            <Glyph name="plus" />{t("folder.newPageHere")}
          </Button>
        ) : null}
      </div>
    );
  }
  // A page's children are its own: they list under the body (or in place of the empty state).
  const children = pages.length ? (
    <PaneRows>
      {pages.map((page) => (
        <PaneRow key={page.id} icon={pageIcon(page.kind)} title={page.title}
          meta={formatDate(page.updated_at)} sub={page.created_by_label ?? undefined}
          onOpen={onOpenPage ? () => onOpenPage(page.id) : undefined} />
      ))}
    </PaneRows>
  ) : null;
  return (
    <div className="mx-auto min-h-0 w-full max-w-prose flex-1 overflow-auto p-6">
      {body && Preview ? (
        <Preview value={body} field={{ label: detail.title }} readOnly />
      ) : children ? null : (
        <EmptyState icon="note" title={t("page.emptyTitle")} description={t("page.emptyDescription")}
          actions={onEdit ? <Button type="button" size="sm" variant="primary" onClick={onEdit}><Glyph name="edit" />{t("page.edit")}</Button> : undefined} />
      )}
      {children ? <div className={body ? "mt-6" : undefined}>{children}</div> : null}
      {children && onNewPageHere ? (
        <Button type="button" size="sm" variant="ghost" className="mt-2" onClick={onNewPageHere}>
          <Glyph name="plus" />{t("folder.newPageHere")}
        </Button>
      ) : null}
    </div>
  );
}

/** The editing body: the markdown editor widget over the autosaving editor state. */
export function PageBodyEditor({ editor }: { editor: PageEditorState }): ReactElement {
  const t = useKnowledgeT();
  const markdown = useResolvedWidget("markdown.editor");
  const Body = markdown?.edit;
  const bodyField = useMemo<WidgetField>(() => ({ name: "body", label: t("editor.bodyPlaceholder") }), [t]);
  return (
    <div className="mx-auto min-h-0 w-full max-w-prose flex-1 overflow-auto p-6">
      {Body ? (
        <Body value={editor.body} field={bodyField} onChange={(next) => editor.setBody(typeof next === "string" ? next : "")} />
      ) : null}
    </div>
  );
}

/** The autosave status beside the band's verbs. */
export function SaveBadge({ status }: { status: SaveStatus }): ReactElement | null {
  const t = useKnowledgeT();
  if (status === "idle") return null;
  if (status === "saving") {
    return (
      <SkeletonStatus label={t("editor.saving")} className="inline-flex min-w-16 items-center">
        <Skeleton className="h-3 w-16" shape="text" />
      </SkeletonStatus>
    );
  }
  if (status === "error") return <span className="text-13 text-danger-text">{t("editor.saveFailed")}</span>;
  return (
    <span className="inline-flex items-center gap-1 text-13 text-success-text">
      <Glyph decorative name="check" />{t("editor.saved")}
    </span>
  );
}
