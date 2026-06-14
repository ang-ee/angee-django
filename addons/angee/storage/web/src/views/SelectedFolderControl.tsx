import { useState, type ReactElement } from "react";

import { Button, Glyph, Input } from "@angee/base";

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
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);

  function submit(): void {
    const trimmed = value.trim();
    if (!trimmed || trimmed === name) {
      setEditing(false);
      return;
    }
    onRename(trimmed);
    setEditing(false);
  }

  if (editing) {
    return (
      <form
        className="flex items-center gap-1"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Input
          autoFocus
          size="sm"
          value={value}
          aria-label={t("storage.folder.nameLabel")}
          onChange={(event) => setValue(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setValue(name);
              setEditing(false);
            }
          }}
        />
        <Button
          type="submit"
          size="sm"
          variant="secondary"
          loading={busy}
          disabled={!value.trim()}
        >
          {t("storage.folder.save")}
        </Button>
      </form>
    );
  }

  return (
    <div className="flex items-center gap-1 px-1">
      <span className="min-w-0 flex-1 truncate text-13 font-medium text-fg">
        {name}
      </span>
      <Button
        type="button"
        size="iconSm"
        variant="ghost"
        aria-label={t("storage.folder.rename")}
        onClick={() => {
          setValue(name);
          setEditing(true);
        }}
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
  );
}
