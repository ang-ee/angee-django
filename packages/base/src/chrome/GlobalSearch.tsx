import { useEffect, useState, type ReactElement } from "react";
import { Command } from "cmdk";

import {
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from "../ui/dialog";
import { Glyph } from "./Glyph";

export interface GlobalSearchProps {
  onSearch?: (query: string) => void;
  placeholder?: string;
}

export function GlobalSearch({
  onSearch = () => undefined,
  placeholder = "Search everywhere",
}: GlobalSearchProps): ReactElement {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.repeat || event.isComposing) return;
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") {
        return;
      }
      if (isTextEntryTarget(event.target)) return;
      event.preventDefault();
      setOpen(true);
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div role="search" aria-label="Global search">
      <button
        type="button"
        aria-label="Open global search"
        onClick={() => setOpen(true)}
        className="flex h-7 w-search-w min-w-0 cursor-text items-center gap-2 rounded-md border border-transparent bg-rail-hi pl-3 pr-2 text-left text-13 text-on-rail-mut outline-none transition-colors hover:border-border-on-rail hover:bg-rail-hover focus-visible:focus-ring [&_.glyph]:size-3.5"
      >
        <Glyph name="search" />
        <span className="min-w-0 flex-1 truncate">{placeholder}</span>
        <kbd className="rounded border border-kbd-on-rail-border bg-kbd-on-rail px-1.5 py-0.5 text-2xs font-medium text-on-rail-mut">
          Cmd K
        </kbd>
      </button>
      <DialogRoot open={open} onOpenChange={setOpen}>
        <DialogPortal>
          <DialogBackdrop />
          <DialogContent aria-label="Global search" placement="default">
            <Command label="Global search" shouldFilter={false}>
              <DialogHeader className="border-b border-border-subtle px-4 py-3">
                <DialogTitle className="sr-only">Global search</DialogTitle>
                <div className="flex h-8 items-center gap-2 text-fg">
                  <Glyph name="search" className="text-fg-muted" />
                  <Command.Input
                    autoFocus
                    value={query}
                    onValueChange={(next) => {
                      setQuery(next);
                      onSearch(next);
                    }}
                    placeholder={placeholder}
                    aria-label={placeholder}
                    className="h-8 min-w-0 flex-1 bg-transparent text-15 outline-none placeholder:text-fg-muted"
                  />
                  <kbd className="rounded border border-border bg-inset px-1.5 py-0.5 text-2xs text-fg-muted">
                    Esc
                  </kbd>
                </div>
              </DialogHeader>
              <DialogBody className="p-0">
                <Command.List className="max-h-modal-list-max-h overflow-y-auto p-2">
                  <Command.Empty className="px-3 py-8 text-center text-13 text-fg-muted">
                    No matching results.
                  </Command.Empty>
                  <Command.Item
                    disabled
                    value="empty"
                    className="flex h-9 items-center rounded-md px-3 text-13 text-fg-muted"
                  >
                    Type a search query to begin.
                  </Command.Item>
                </Command.List>
              </DialogBody>
            </Command>
          </DialogContent>
        </DialogPortal>
      </DialogRoot>
    </div>
  );
}

function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.matches("input, textarea, select, [contenteditable='true']");
}
