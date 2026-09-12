import { Button, EmptyState, Glyph, useResolvedWidget } from "@angee/ui";
import type { ReactElement } from "react";

import type { KnowledgePageDetail } from "../data/documents";
import { useKnowledgeT } from "../i18n";

/** Read-first knowledge page presentation; editing is an explicit transition. */
export function PageReader({
  detail,
  onEdit,
  onDelete,
}: {
  detail: KnowledgePageDetail;
  onEdit: () => void;
  onDelete: () => void;
}): ReactElement {
  const t = useKnowledgeT();
  const markdown = useResolvedWidget("markdown.preview");
  const Preview = markdown?.read;
  const body = detail.markdown?.body ?? "";

  return (
    <article className="mx-auto flex h-full w-full max-w-[820px] flex-col gap-6 overflow-auto px-8 py-8">
      <header className="flex items-start gap-4">
        <Glyph
          decorative
          name={detail.kind === "folder" ? "folder" : "note"}
          className="mt-1 shrink-0 text-fg-muted"
        />
        <div className="min-w-0 flex-1">
          <h1 className="text-28 font-semibold leading-9 text-fg">
            {detail.title || t("editor.titlePlaceholder")}
          </h1>
          <p className="mt-1 text-12 text-fg-muted">
            {t("page.lastUpdated", { value: detail.updated_at })}
          </p>
        </div>
        <Button type="button" size="sm" variant="secondary" onClick={onEdit}>
          <Glyph name="edit" />
          {t("page.edit")}
        </Button>
        <Button type="button" size="iconSm" variant="ghost" aria-label={t("editor.deleteLabel")} onClick={onDelete}>
          <Glyph name="trash" />
        </Button>
      </header>
      {detail.kind === "folder" ? (
        <EmptyState icon="folder" title={t("editor.folderTitle")} description={t("editor.folderDescription")} />
      ) : body && Preview ? (
        <Preview value={body} field={{ label: detail.title }} readOnly />
      ) : (
        <EmptyState icon="note" title={t("page.emptyTitle")} description={t("page.emptyDescription")} />
      )}
    </article>
  );
}
