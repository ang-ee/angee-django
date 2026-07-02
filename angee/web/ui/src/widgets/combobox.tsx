import { useMemo, useState, type ReactElement, type ReactNode } from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import {
  SelectIcon,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectList,
  SelectPrimitive,
  SelectValue,
} from "../ui/select";
import { textRoleVariants } from "../ui/text";
import { widgetLabel } from "./label";
import {
  canonicalOptionValue,
  optionLabel,
  optionTextLabel,
  type WidgetDefinition,
  type WidgetOption,
  type WidgetRenderProps,
} from "./types";

function ComboboxEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const t = useUiT();
  const [query, setQuery] = useState("");
  const options = field?.options ?? [];
  const labels = useMemo(() => optionLabelMap(options), [options]);
  const visibleOptions = useMemo(
    () => filterOptions(options, query),
    [options, query],
  );
  const selected = canonicalOptionValue(options, value) ?? value ?? "";

  return (
    <SelectPrimitive.Root
      items={visibleOptions}
      value={selected}
      readOnly={readOnly}
      disabled={readOnly}
      onOpenChange={(open) => {
        if (!open) setQuery("");
      }}
      onValueChange={(next) => onChange?.(next ?? "")}
    >
      <SelectPrimitive.Trigger
        aria-label={widgetLabel(field, t("combobox.label"))}
        readOnly={readOnly}
      >
        <SelectValue>
          {(selected) =>
            labels.get(String(selected ?? "")) ??
            widgetLabel(field, t("combobox.selectOption"))
          }
        </SelectValue>
        <SelectIcon />
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Positioner sideOffset={4}>
          <SelectPrimitive.Content>
            <label className="flex h-8 items-center gap-2 border-b border-border-subtle px-2 text-fg-muted">
              <Glyph name="search" className="shrink-0" />
              <input
                type="search"
                value={query}
                aria-label={t("combobox.searchOptions")}
                className="min-w-0 flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted"
                placeholder={t("combobox.search")}
                onChange={(event) => setQuery(event.currentTarget.value)}
                onKeyDown={(event) => event.stopPropagation()}
              />
            </label>
            <SelectList>
              {visibleOptions.length > 0 ? (
                visibleOptions.map((option) => (
                  <SelectItem
                    key={option.value}
                    value={option.value}
                    disabled={option.disabled}
                    label={optionTextLabel(option.label)}
                  >
                    <SelectItemText>{option.label}</SelectItemText>
                    <SelectItemIndicator />
                  </SelectItem>
                ))
              ) : (
                <div className={cn(textRoleVariants({ role: "meta" }), "px-2 py-3")}>{t("combobox.noOptions")}</div>
              )}
            </SelectList>
          </SelectPrimitive.Content>
        </SelectPrimitive.Positioner>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

function ComboboxRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const selected = canonicalOptionValue(field?.options, value) ?? value;
  const label = optionLabel(field?.options, selected);
  return <span className="text-13 text-fg">{label}</span>;
}

export const comboboxWidget = {
  edit: ComboboxEdit,
  read: ComboboxRead,
  cell: ComboboxRead,
} satisfies WidgetDefinition<string>;

function optionLabelMap(
  options: readonly WidgetOption[],
): Map<string, ReactNode> {
  return new Map(options.map((option) => [option.value, option.label]));
}

function filterOptions(
  options: readonly WidgetOption[],
  query: string,
): readonly WidgetOption[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return options;
  return options.filter((option) => {
    const haystack = `${option.value} ${optionTextLabel(option.label)}`;
    return haystack.toLowerCase().includes(normalized);
  });
}
