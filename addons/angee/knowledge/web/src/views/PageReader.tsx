import { Button, ControlBand, EmptyState, Glyph, SurfaceHeader, formatDateTime, useResolvedWidget } from "@angee/ui";
import type { ReactElement, Ref } from "react";

import type { KnowledgePageDetail } from "../data/documents";
import { useKnowledgeT } from "../i18n";

/** Read-first knowledge page presentation; editing is an explicit transition. */
export function PageReader({
  detail,
  onEdit,
  editButtonRef,
  onDelete,
}: {
  detail: KnowledgePageDetail;
  onEdit?: () => void;
  editButtonRef?: Ref<HTMLButtonElement>;
  onDelete?: () => void;
}): ReactElement {
  const t = useKnowledgeT();
  const markdown = useResolvedWidget("markdown.preview");
  const Preview = markdown?.read;
  const body = detail.markdown?.body ?? "";

  return (
    <article className="flex h-full min-h-0 flex-col bg-canvas">
      {onEdit || onDelete ? <ControlBand>
        {onEdit ? (
          <Button ref={editButtonRef} type="button" size="sm" variant="secondary" onClick={onEdit}>
            <Glyph name="edit" />
            {t("page.edit")}
          </Button>
        ) : null}
        {onDelete ? (
          <Button type="button" size="iconSm" variant="ghost" aria-label={t("editor.deleteLabel")} onClick={onDelete}>
            <Glyph name="trash" />
          </Button>
        ) : null}
      </ControlBand> : null}
      <SurfaceHeader density="compact" icon={detail.kind === "folder" ? "folder" : "note"}
        title={detail.title || t("editor.titlePlaceholder")}
        subtitle={t("page.lastUpdated", { value: formatDateTime(detail.updated_at) })} />
      <div className="mx-auto min-h-0 w-full max-w-[820px] flex-1 overflow-auto p-6">
        {detail.kind === "folder" ? (
          <EmptyState icon="folder" title={t("editor.folderTitle")} description={t("editor.folderDescription")} />
        ) : body && Preview ? (
          <Preview value={body} field={{ label: detail.title }} readOnly />
        ) : (
          <EmptyState icon="note" title={t("page.emptyTitle")} description={t("page.emptyDescription")} />
        )}
      </div>
    </article>
  );
}
