import { useState, type ReactElement } from "react";

import { Button, Glyph, Input } from "@angee/base";

import { useStorageT } from "../i18n";

export interface NewFolderControlProps {
  busy: boolean;
  /** Create a folder with the entered name (already trimmed, non-empty). */
  onCreate: (name: string) => void;
}

/**
 * The navigator's "New folder" affordance: a button that expands into an inline
 * name field (Enter creates, Escape/blur-when-empty cancels). The page decides
 * which drive/parent the new folder lands in from the active scope.
 */
export function NewFolderControl({
  busy,
  onCreate,
}: NewFolderControlProps): ReactElement {
  const t = useStorageT();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  function submit(): void {
    const trimmed = name.trim();
    if (!trimmed) return;
    onCreate(trimmed);
    setName("");
    setOpen(false);
  }

  if (!open) {
    return (
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="justify-start"
        onClick={() => setOpen(true)}
      >
        <Glyph name="folder" />
        {t("storage.newFolder.button")}
      </Button>
    );
  }

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
        value={name}
        placeholder={t("storage.newFolder.placeholder")}
        aria-label={t("storage.newFolder.nameLabel")}
        onChange={(event) => setName(event.currentTarget.value)}
        onBlur={() => {
          if (!name.trim()) setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setName("");
            setOpen(false);
          }
        }}
      />
      <Button
        type="submit"
        size="sm"
        variant="secondary"
        loading={busy}
        disabled={!name.trim()}
      >
        {t("storage.newFolder.create")}
      </Button>
    </form>
  );
}
