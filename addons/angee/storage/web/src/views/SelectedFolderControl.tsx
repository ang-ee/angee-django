import type { ReactElement } from "react";

import { Button, Glyph, InlineTextAction } from "@angee/ui";

import { useStorageT } from "../i18n";

export interface SelectedFolderControlProps {
  name: string;
  busy: boolean;
  /** Rename the folder to the entered name (already trimmed, changed). */
  onRename: (name: string) => void;
  /** Delete the folder (the page confirms first). */
  onDelete: () => void;
}

/**
 * Actions for the active folder scope, in the navigator footer: rename it inline
 * or delete it. Mount with a `key` of the folder id so switching folders resets
 * the edit state.
 */
export function SelectedFolderControl({
  name,
  busy,
  onRename,
  onDelete,
}: SelectedFolderControlProps): ReactElement {
  const t = useStorageT();

  return (
    <InlineTextAction
      value={name}
      busy={busy}
      onSubmit={onRename}
      inputLabel={t("storage.folder.nameLabel")}
      submitLabel={t("storage.folder.save")}
      className="px-1"
      formClassName="w-full items-center gap-1"
      inputClassName="min-w-0 flex-1"
      renderTrigger={({ open }) => (
        <div className="flex items-center gap-1">
          <span className="min-w-0 flex-1 truncate text-13 font-medium text-fg">
            {name}
          </span>
          <Button
            type="button"
            size="iconSm"
            variant="ghost"
            aria-label={t("storage.folder.rename")}
            onClick={open}
          >
            <Glyph name="edit" />
          </Button>
          <Button
            type="button"
            size="iconSm"
            variant="ghost"
            aria-label={t("storage.folder.delete")}
            onClick={onDelete}
          >
            <Glyph name="trash" />
          </Button>
        </div>
      )}
    />
  );
}
