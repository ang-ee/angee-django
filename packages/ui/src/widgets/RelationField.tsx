import { lazy, useMemo, useState, type ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { LazyBoundary } from "../fragments/LazyBoundary";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { Skeleton } from "../ui/skeleton";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";

// cmdk (and the option list it renders) loads when the picker first opens, not
// at boot — the popover mounts this body only while open.
const RelationFieldCommandList = lazy(() => import("./RelationFieldCommandList"));

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
  /**
   * Notified whenever the picker popover opens or closes. Lets a caller defer
   * work (e.g. fetching the option list) until the popover is first opened;
   * the internal open state is unaffected when this is omitted.
   */
  onOpenChange?: (open: boolean) => void;
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
  onOpenChange,
}: RelationFieldProps): ReactElement {
  const t = useBaseT();
  const [open, setOpen] = useState(false);

  const selected = useMemo(
    () => options.find((option) => option.value === value),
    [options, value],
  );
  const triggerLabel = selected
    ? ariaLabel
      ? `${ariaLabel}: ${selected.label}`
      : selected.label
    : ariaLabel;

  return (
    <PopoverRoot
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        onOpenChange?.(next);
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
            <LazyBoundary pending={<Skeleton className="m-1 h-8" />}>
              <RelationFieldCommandList
                options={options}
                value={value}
                searchPlaceholder={searchPlaceholder}
                aria-label={ariaLabel}
                onSelect={(next) => onChange?.(next)}
                onCreate={onCreate}
                onDismiss={() => setOpen(false)}
              />
            </LazyBoundary>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
