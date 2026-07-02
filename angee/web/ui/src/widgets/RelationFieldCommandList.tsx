import { useMemo, useState, type ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { Command } from "../ui/command";
import type { RelationOption } from "./RelationField";

export interface RelationFieldCommandListProps {
  options: readonly RelationOption[];
  value?: string | null;
  searchPlaceholder?: string;
  /** Accessible name for the command list. */
  "aria-label"?: string;
  onSelect: (value: string) => void;
  /** When set, a "Create …" row appears for a typed query that matches nothing. */
  onCreate?: (query: string) => void;
  /** Close the popover after a selection or create. */
  onDismiss: () => void;
}

/**
 * The cmdk-backed body of the {@link RelationField} popover — search box, the
 * filtered options, and the optional "Create …" row — code-split so cmdk loads
 * only when the picker is first opened, not at boot. Owns its own query state so
 * the search resets each time the popover (and this module) remounts.
 */
export default function RelationFieldCommandList({
  options,
  value,
  searchPlaceholder,
  "aria-label": ariaLabel,
  onSelect,
  onCreate,
  onDismiss,
}: RelationFieldCommandListProps): ReactElement {
  const t = useUiT();
  const [query, setQuery] = useState("");
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

  return (
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
              onSelect(option.value);
              onDismiss();
            }}
          >
            <span className="min-w-0 flex-1 truncate">{option.label}</span>
            {option.value === value ? <Glyph decorative name="check" /> : null}
          </Command.Item>
        ))}
        {showCreate ? (
          <Command.Item
            value="__create__"
            onSelect={() => {
              onCreate?.(query.trim());
              onDismiss();
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
  );
}
