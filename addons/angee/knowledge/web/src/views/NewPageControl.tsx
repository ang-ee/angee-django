import { useState, type ReactElement } from "react";

import { Button, Glyph, InlineTextAction } from "@angee/ui";

import { useKnowledgeT } from "../i18n";

export type NewPageKind = "note" | "folder";

export interface NewPageControlProps {
  busy: boolean;
  /** Create a page of the chosen kind with the entered title (trimmed). */
  onCreate: (kind: NewPageKind, title: string) => void;
}

/**
 * The navigator's create affordance: a New note button plus a folder button.
 * Either expands into an inline title field. The page decides which vault/parent
 * the new page lands in.
 */
export function NewPageControl({
  busy,
  onCreate,
}: NewPageControlProps): ReactElement {
  const t = useKnowledgeT();
  const [kind, setKind] = useState<NewPageKind | null>(null);

  return (
    <InlineTextAction
      open={kind !== null}
      busy={busy}
      onOpenChange={(next) => {
        if (!next) setKind(null);
      }}
      onSubmit={(title) => {
        if (kind) onCreate(kind, title);
      }}
      inputLabel={t("knowledge.newPage.titleLabel")}
      placeholder={
        kind === "folder"
          ? t("knowledge.newPage.folderPlaceholder")
          : t("knowledge.newPage.notePlaceholder")
      }
      submitLabel={t("knowledge.newPage.create")}
      formClassName="items-center gap-1"
      renderTrigger={({ busy: actionBusy, open }) => (
        <div className="flex items-center gap-1">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="flex-1 justify-start"
            disabled={actionBusy}
            onClick={() => {
              setKind("note");
              open();
            }}
          >
            <Glyph name="note" />
            {t("knowledge.newPage.newNote")}
          </Button>
          <Button
            type="button"
            size="iconSm"
            variant="ghost"
            aria-label={t("knowledge.newPage.newFolder")}
            disabled={actionBusy}
            onClick={() => {
              setKind("folder");
              open();
            }}
          >
            <Glyph name="folder" />
          </Button>
        </div>
      )}
    />
  );
}
