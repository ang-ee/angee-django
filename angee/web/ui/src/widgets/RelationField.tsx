import { useMemo, useState, type ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { Command } from "../ui/command";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";

/** One selectable related record. */
export interface RelationOption {
  value: string;
  label: string;
}

export interface RelationFieldProps {
  value?: string | null;
  onChange?: (value: string) => void;
  options: readonly RelationOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  readOnly?: boolean;
  /** Accessible name for the trigger; the selected value is appended to it. */
  "aria-label"?: string;
  /**
   * When set, the popover offers a "Create …" row for the typed query whenever
   * it matches no option — the searchable, in-place create affordance.
   */
  onCreate?: (query: string) => void;
}

const TRIGGER_CLASS =
  "flex h-9 w-full items-center gap-2 rounded-6 border border-border bg-sheet px-3 " +
  "text-left text-13 text-fg outline-none transition-colors hover:border-border-strong " +
  "focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60";

/**
 * A searchable relation picker: a trigger showing the selected record, and a
 * popover (the owned `Command` list) with a search box, the filtered options,
 * and — when `onCreate` is given and the typed query matches nothing — a
 * "Create …" row. Pure UI; the caller owns the options and what happens on
 * create (`RelationPicker` wires it to an inline create form).
 */
export function RelationField({
  value,
  onChange,
  options,
  placeholder,
  searchPlaceholder,
  readOnly,
  "aria-label": ariaLabel,
  onCreate,
}: RelationFieldProps): ReactElement {
  const t = useBaseT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const selected = useMemo(
    () => options.find((option) => option.value === value),
    [options, value],
  );
  const normalized = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!normalized) return options;
    return options.filter((option) =>
      `${option.label} ${option.value}`.toLowerCase().includes(normalized),
    );
  }, [options, normalized]);
  const exactMatch = options.some(
    (option) => option.label.trim().toLowerCase() === normalized,
  );
  const showCreate = Boolean(onCreate) && normalized.length > 0 && !exactMatch;
  const triggerLabel = selected
    ? ariaLabel
      ? `${ariaLabel}: ${selected.label}`
      : selected.label
    : ariaLabel;

  function dismiss(): void {
    setOpen(false);
    setQuery("");
  }

  return (
    <PopoverRoot
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <PopoverTrigger
        className={TRIGGER_CLASS}
        disabled={readOnly}
        aria-label={triggerLabel}
      >
        <span className={cn("min-w-0 flex-1 truncate", !selected && "text-fg-muted")}>
          {selected ? selected.label : (placeholder ?? t("relation.placeholder"))}
        </span>
        <Glyph decorative name="chevron-down" className="shrink-0 text-fg-muted" />
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner sideOffset={4} align="start">
          <PopoverContent className="min-w-56 p-0">
            <Command shouldFilter={false} label={ariaLabel}>
              <Command.Search>
                <Command.Input
                  autoFocus
                  value={query}
                  onValueChange={setQuery}
                  placeholder={searchPlaceholder ?? t("relation.searchPlaceholder")}
                />
              </Command.Search>
              <Command.List>
                {filtered.map((option) => (
                  <Command.Item
                    key={option.value}
                    value={option.value}
                    onSelect={() => {
                      onChange?.(option.value);
                      dismiss();
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">{option.label}</span>
                    {option.value === value ? (
                      <Glyph decorative name="check" />
                    ) : null}
                  </Command.Item>
                ))}
                {showCreate ? (
                  <Command.Item
                    value="__create__"
                    onSelect={() => {
                      onCreate?.(query.trim());
                      dismiss();
                    }}
                    className="text-brand-soft-text"
                  >
                    <Glyph decorative name="plus" />
                    <span className="min-w-0 flex-1 truncate">
                      {t("relation.create", { query: query.trim() })}
                    </span>
                  </Command.Item>
                ) : null}
                {filtered.length === 0 && !showCreate ? (
                  <Command.Empty>{t("relation.noMatches")}</Command.Empty>
                ) : null}
              </Command.List>
            </Command>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
