import type { ReactElement } from "react";

import { Button, Glyph, InlineTextAction } from "@angee/ui";

import { useStorageT } from "../i18n";

export interface NewFolderControlProps {
  busy: boolean;
  /** Create a folder with the entered name (already trimmed, non-empty). */
  onCreate: (name: string) => void;
}

/**
 * The navigator's "New folder" affordance: a button that expands into an inline
 * name field. The page decides which drive/parent the new folder lands in from
 * the active scope.
 */
export function NewFolderControl({
  busy,
  onCreate,
}: NewFolderControlProps): ReactElement {
  const t = useStorageT();

  return (
    <InlineTextAction
      busy={busy}
      onSubmit={onCreate}
      inputLabel={t("storage.newFolder.nameLabel")}
      placeholder={t("storage.newFolder.placeholder")}
      submitLabel={t("storage.newFolder.create")}
      formClassName="items-center gap-1"
      renderTrigger={({ busy: actionBusy, open }) => (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="justify-start"
          disabled={actionBusy}
          onClick={open}
        >
          <Glyph name="folder" />
          {t("storage.newFolder.button")}
        </Button>
      )}
    />
  );
}
