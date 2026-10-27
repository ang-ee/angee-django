import { useRef, useState, type ReactElement, type Ref } from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { Combobox } from "../ui/combobox";
import type { WidgetControlProps } from "./types";
import type { Tone } from "../lib/tones";

/** One selectable related record. */
export interface RelationOption {
  value: string;
  label: string;
  /** The related record's own `#rrggbb` colour, when its model has a record colour. */
  color?: string;
  /** Secondary option text projected from a caller-declared related-row field. */
  description?: string;
  /** Status value presented with the shared status vocabulary and tone owner. */
  status?: { value: string; label: string; tone: Tone };
}

export interface RelationSearchState {
  pending?: boolean;
  error?: string;
  retry?: () => void;
}

export interface RelationFieldProps extends Pick<WidgetControlProps, "presentation"> {
  value?: string | null;
  onChange?: (value: string) => void;
  options: readonly RelationOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  readOnly?: boolean;
  /** Accessible name for the trigger; the selected value is appended to it. */
  "aria-label"?: string;
  "aria-labelledby"?: string;
  id?: string;
  "aria-describedby"?: string;
  "aria-required"?: boolean;
  "aria-invalid"?: boolean;
  /**
   * When set, the popover offers a "Create …" row for the typed query whenever
   * it matches no option — the searchable, in-place create affordance.
   */
  onCreate?: (query: string) => void;
  /**
   * Notified whenever the picker popover opens or closes. Lets a caller defer
   * work (e.g. fetching the option list) until the popover is first opened;
   * the internal open state is unaffected when this is omitted.
   */
  onOpenChange?: (open: boolean) => void;
  /** A remote collection owns search when supplied. */
  onSearchChange?: (query: string) => void;
  searchState?: RelationSearchState;
  triggerRef?: Ref<HTMLButtonElement>;
}

/**
 * A searchable relation picker: a trigger showing the selected record, and a
 * Combobox popup with a search box, the filtered options,
 * and — when `onCreate` is given and the typed query matches nothing — a
 * "Create …" row. Pure UI; the caller owns the options and what happens on
 * create (`RelationPicker` wires it to an inline create form).
 * @kind surface
 */
export function RelationField({
  value,
  onChange,
  options,
  placeholder,
  searchPlaceholder,
  readOnly,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
  "aria-required": ariaRequired,
  "aria-invalid": ariaInvalid,
  id,
  onCreate,
  onOpenChange,
  onSearchChange,
  searchState,
  triggerRef,
  presentation,
}: RelationFieldProps): ReactElement {
  const t = useUiT();
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const selected = options.find((option) => option.value === value);
  const text = query.trim();
  const normalized = text.toLowerCase();
  const showCreate = Boolean(onCreate) && text !== ""
    && !options.some((option) => option.label.trim().toLowerCase() === normalized)
    && !searchState?.pending && !searchState?.error;
  // The create action is an object identity, never a reserved record id.
  const createOption = { create: true as const, value: "", label: t("relation.create", { query: text }) };
  type Item = RelationOption | typeof createOption;
  const items: Item[] = [
    ...options,
    ...(showCreate ? [createOption] : []),
  ];
  const triggerLabel = selected
    ? ariaLabel ? `${ariaLabel}: ${selected.label}` : selected.label
    : ariaLabel;

  return (
    <Combobox.Root<Item>
      autoHighlight
      required={ariaRequired}
      readOnly={readOnly}
      value={selected ?? (value ? { value, label: value } : null)}
      items={items}
      filteredItems={searchState?.pending || searchState?.error ? [] : undefined}
      isItemEqualToValue={(option, current) => !("create" in option) && !("create" in current) && option.value === current.value}
      filter={onSearchChange ? null : (option, search) => "create" in option ||
        `${option.label} ${option.value} ${option.description ?? ""} ${option.status?.label ?? ""}`.toLowerCase().includes(search.trim().toLowerCase())}
      inputValue={query}
      onInputValueChange={(next, details) => {
        if (details.reason !== "input-change" && details.reason !== "input-clear") return;
        setQuery(next);
        onSearchChange?.(next);
      }}
      onOpenChange={(next) => {
        if (!next) setQuery("");
        onOpenChange?.(next);
      }}
      onValueChange={(next, details) => {
        if (details.reason === "escape-key") { details.allowPropagation(); return; }
        if (!next) return;
        if ("create" in next) onCreate?.(text);
        else onChange?.(next.value);
      }}
    >
      <Combobox.Trigger
        ref={triggerRef}
        id={id}
        presentation={presentation}
        invalid={ariaInvalid}
        readOnly={readOnly}
        disabled={readOnly}
        aria-label={ariaLabelledBy ? undefined : triggerLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        aria-required={ariaRequired}
        aria-invalid={ariaInvalid}
      >
        <Combobox.Value placeholder={placeholder ?? t("relation.placeholder")}>
          {() => selected?.label ?? (value || placeholder || t("relation.placeholder"))}
        </Combobox.Value>
        <Combobox.Icon />
      </Combobox.Trigger>
      <Combobox.Popup align="start" className="min-w-56 p-0" initialFocus={searchRef}>
        <Combobox.Search ref={searchRef} placeholder={searchPlaceholder ?? t("relation.searchPlaceholder")} />
        {searchState?.pending ? <div role="status" className="p-3 text-13 text-fg-muted">
          {t("relation.loading")}
        </div> : null}
        {searchState?.error ? <div role="alert" className="p-3 text-13 text-danger-text">
          {searchState.error}
          {searchState.retry ? <Button size="sm" variant="ghost" onClick={searchState.retry}>
            {t("collection.retry")}
          </Button> : null}
        </div> : null}
        <Combobox.List>
          {(option: Item) => <Combobox.Item key={"create" in option ? "create" : `record:${option.value}`} value={option}>
            {"create" in option ? <><Glyph decorative name="plus" /><Combobox.ItemText>{option.label}</Combobox.ItemText></> : <>
              <Combobox.ItemText>
                <span className="block truncate">{option.label}</span>
                {option.description ? <span className="block truncate text-2xs text-fg-muted">{option.description}</span> : null}
              </Combobox.ItemText>
              {option.status ? <Chip className="shrink-0" tone={option.status.tone}>{option.status.label}</Chip> : null}
              <Combobox.ItemIndicator />
            </>}
          </Combobox.Item>}
        </Combobox.List>
        {!showCreate && !searchState?.pending && !searchState?.error ? <Combobox.Empty>{t("relation.noMatches")}</Combobox.Empty> : null}
      </Combobox.Popup>
    </Combobox.Root>
  );
}
