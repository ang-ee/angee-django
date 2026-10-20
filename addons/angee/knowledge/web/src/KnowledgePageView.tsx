import { useAuthoredQuery } from "@angee/refine";
import { holdsPermission, useModelMetadata, type Row } from "@angee/metadata";
import {
  Button, CommandBand, ErrorBanner, Glyph, Input, RecordHeader, Skeleton, SkeletonStatus, SkeletonText, cn,
  errorMessage, formatDate, formatNumber, textRoleVariants, type RecordChromeContext, type RecordNavigation,
} from "@angee/ui";
import { useCallback, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";

import { KnowledgePage, PAGE_MODEL, PAGE_READ_MODELS, type KnowledgePageDetail, type KnowledgePageRow } from "./data/documents";
import { usePageEditor } from "./data/use-page-editor";
import { useKnowledgeT } from "./i18n";
import { PageBody, PageBodyEditor, SaveBadge } from "./views/PageBody";

/** The wiki page frames the view with the command band and record header; embedded hosts omit it. */
export interface KnowledgePageFrame {
  /** The eyebrow: kind and container path ("Page · Accounts Payable / Supplier contracts"). */
  crumbs: ReactNode;
  navigation: RecordNavigation | null;
  /** A folder's pages, in tree order, for the folder body. */
  children: readonly KnowledgePageRow[];
  onOpenPage: (id: string) => void;
  /** Starts a new page inside this folder, from the folder body. */
  onNewPageHere?: () => void;
}

/** `[[wikilinks]]` resolve only under a wikilink resolver provider. */
export interface KnowledgePageViewProps {
  pageId: string;
  /** An enclosing page may supply its existing trash action. */
  onDelete?: () => void;
  frame?: KnowledgePageFrame;
}

/** Embed one actor-readable page and its edit transition. */
export function KnowledgePageView(props: KnowledgePageViewProps): ReactElement {
  return <KnowledgePageContent key={props.pageId} {...props} />;
}

function KnowledgePageContent({ pageId, onDelete, frame }: KnowledgePageViewProps): ReactElement | null {
  const t = useKnowledgeT();
  const query = useAuthoredQuery(KnowledgePage, { id: pageId }, { models: PAGE_READ_MODELS });
  const detail = query.data?.pages_by_pk;
  if (!detail) {
    if (query.isPending) return <SkeletonStatus label={t("page.loading")} className="space-y-3 p-4">
      <Skeleton size="lg" className="w-2/3" /><SkeletonText lines={2} />
    </SkeletonStatus>;
    if (query.error) return <ErrorBanner description={errorMessage(query.error, t("page.loadError"))} />;
    return null;
  }
  return <PageSubject key={detail.id} detail={detail} onDelete={onDelete} frame={frame} />;
}

/**
 * One loaded page as the subject: the command band (menu · Edit/Done · header
 * controls · pager), the record header (eyebrow · title · facts), then the body.
 * Editing is an explicit transition; the title edits in place in the header.
 */
function PageSubject({ detail, onDelete, frame }: {
  detail: KnowledgePageDetail;
  onDelete?: () => void;
  frame?: KnowledgePageFrame;
}): ReactElement {
  const t = useKnowledgeT();
  const metadata = useModelMetadata(PAGE_MODEL);
  const canWrite = holdsPermission(detail, "write");
  const [editing, setEditing] = useState(false);
  // Done returns focus to Edit, which remounts in the band when editing ends.
  const restoreEditFocus = useRef(false);
  const editButtonRef = useCallback((button: HTMLButtonElement | null) => {
    if (button && restoreEditFocus.current) {
      button.focus();
      restoreEditFocus.current = false;
    }
  }, []);
  const editor = usePageEditor(detail.id, {
    title: detail.title,
    body: detail.markdown?.body ?? "",
    bodyHash: detail.markdown?.body_hash ?? "",
  });
  const isNote = detail.kind !== "folder";
  // Losing write while editing ends the edit; it does not resume when write returns.
  if (editing && !canWrite) {
    restoreEditFocus.current = false;
    setEditing(false);
  }
  const active = editing && canWrite;
  const chrome = useMemo<RecordChromeContext>(() => ({
    resource: PAGE_MODEL,
    dataProviderName: metadata?.resource.schemaName,
    canonicalResource: PAGE_MODEL,
    recordId: detail.id,
    record: detail as unknown as Row,
    formReadOnly: !canWrite,
  }), [canWrite, detail, metadata?.resource.schemaName]);
  const facts = [
    detail.created_by_label
      ? t("page.facts", { date: formatDate(detail.updated_at), by: detail.created_by_label })
      : t("page.lastUpdated", { value: formatDate(detail.updated_at) }),
    ...(detail.markdown ? [t("editor.wordCount", { count: formatNumber(detail.markdown.word_count) })] : []),
  ].join(" · ");

  return (
    <article className="flex h-full min-h-0 flex-col bg-canvas">
      <CommandBand
        verbs={<>
          {canWrite ? (active ? (
            <Button type="button" size="sm" variant="primary" onClick={() => { editor.commitTitle(); restoreEditFocus.current = true; setEditing(false); }}>
              <Glyph name="check" />{t("editor.done")}
            </Button>
          ) : (
            <Button ref={editButtonRef} type="button" size="sm" variant="primary" onClick={() => setEditing(true)}>
              <Glyph name="edit" />{t("page.edit")}
            </Button>
          )) : null}
          {onDelete ? (
            <Button type="button" size="sm" variant="danger" onClick={onDelete}>
              <Glyph name="trash" />{t("editor.deleteLabel")}
            </Button>
          ) : null}
        </>}
        chrome={chrome}
        navigation={frame?.navigation ?? null}
        end={active ? <SaveBadge status={editor.status} /> : null}
      />
      <RecordHeader
          density="compact"
          headingLevel={active ? null : 1}
          crumbs={frame?.crumbs}
          title={active ? (
            // The title edits in place, in the display role: the plain presentation keeps the
            // field's focus ring and placeholder and drops the box.
            <Input
              autoFocus
              presentation="cell"
              value={editor.title}
              placeholder={t("editor.titlePlaceholder")}
              aria-label={t("editor.titleLabel")}
              className={cn(textRoleVariants({ role: "display" }), "h-auto px-0")}
              onChange={(event) => editor.setTitle(event.currentTarget.value)}
              onBlur={editor.commitTitle}
            />
          ) : (detail.title || t("editor.titlePlaceholder"))}
          meta={facts}
        />
      {active && isNote ? (
        <PageBodyEditor editor={editor} />
      ) : (
        <PageBody
          detail={detail}
          pages={frame?.children ?? []}
          onOpenPage={frame?.onOpenPage}
          onNewPageHere={frame?.onNewPageHere}
          onEdit={canWrite ? () => setEditing(true) : undefined}
        />
      )}
    </article>
  );
}
