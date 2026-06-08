import { useState, type ReactElement } from "react";

import { Button, Glyph, Input } from "@angee/base";

export type NewPageKind = "note" | "folder";

export interface NewPageControlProps {
  busy: boolean;
  /** Create a page of the chosen kind with the entered title (trimmed). */
  onCreate: (kind: NewPageKind, title: string) => void;
}

/**
 * The navigator's create affordance: a New note button plus a folder button.
 * Either expands into an inline title field (Enter creates, Escape/empty-blur
 * cancels). The page decides which vault/parent the new page lands in.
 */
export function NewPageControl({
  busy,
  onCreate,
}: NewPageControlProps): ReactElement {
  const [kind, setKind] = useState<NewPageKind | null>(null);
  const [title, setTitle] = useState("");

  function submit(): void {
    const trimmed = title.trim();
    if (!trimmed || !kind) return;
    onCreate(kind, trimmed);
    setTitle("");
    setKind(null);
  }

  if (!kind) {
    return (
      <div className="flex items-center gap-1">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="flex-1 justify-start"
          onClick={() => setKind("note")}
        >
          <Glyph name="note" />
          New note
        </Button>
        <Button
          type="button"
          size="iconSm"
          variant="ghost"
          aria-label="New folder"
          onClick={() => setKind("folder")}
        >
          <Glyph name="folder" />
        </Button>
      </div>
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
        value={title}
        placeholder={kind === "folder" ? "Folder name" : "Note title"}
        aria-label="New page title"
        onChange={(event) => setTitle(event.currentTarget.value)}
        onBlur={() => {
          if (!title.trim()) setKind(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setTitle("");
            setKind(null);
          }
        }}
      />
      <Button
        type="submit"
        size="sm"
        variant="secondary"
        loading={busy}
        disabled={!title.trim()}
      >
        Create
      </Button>
    </form>
  );
}
