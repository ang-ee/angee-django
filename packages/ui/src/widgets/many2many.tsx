import type { ReactElement } from "react";

import { Chip, RemovableChip } from "../ui/chip";
import { Select, SelectPrimitive, SelectValue, SelectIcon, SelectList, SelectItem, SelectItemText, SelectItemIndicator } from "../ui/select";
import { textRoleVariants } from "../ui/text";
import { widgetLabel } from "./label";
import {
  optionLabel,
  optionTextLabel,
  relationValueId,
  type WidgetDefinition,
  type WidgetOption,
  type WidgetRenderProps,
} from "./types";

export function Many2ManyEdit({
  value,
  onChange,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<readonly unknown[]>): ReactElement {
  const selected = normaliseValues(value);
  const options = field?.options ?? [];
  const available = options.filter((option) => !selected.includes(option.value));

  if (readOnly) return <Many2ManyRead value={selected} field={field} />;

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Many2ManyChips
        values={selected}
        options={options}
        onRemove={(next) => onChange?.(next)}
      />
      <Select
        triggerRef={controlRef}
        value=""
        options={available}
        disabled={available.length === 0}
        aria-label={widgetLabel(field, "Related records")}
        placeholder={
          available.length === 0
            ? "All records selected"
            : widgetLabel(field, "Add record")
        }
        onValueChange={(next) => {
          if (next) onChange?.([...selected, next]);
        }}
      />
    </div>
  );
}

export function Many2ManyCellEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<readonly unknown[]>): ReactElement {
  const selected = normaliseValues(value);
  const options = field?.options ?? [];
  // Retain selected ids outside the loaded option window. They remain visible
  // and removable; opening the picker must never silently drop a stored relation.
  const choices: readonly WidgetOption[] = [
    ...options,
    ...selected.filter((id) => !options.some((option) => option.value === id))
      .map((id) => ({ value: id, label: id })),
  ];

  if (readOnly) return <Many2ManyRead value={selected} field={field} />;

  const summary = selected.map((id) => optionTextLabel(optionLabel(options, id), id)).join(", ");
  return (
    <SelectPrimitive.Root<string, true>
      multiple
      value={selected}
      items={choices}
      onValueChange={(next) => onChange?.(next)}
    >
      <SelectPrimitive.Trigger
        {...field?.controlProps}
        disabled={choices.length === 0}
        aria-label={widgetLabel(field, "Related records")}
        title={summary || undefined}
      >
        <SelectValue>
          {() => selected.length ? (
            <span className="flex min-w-0 items-center gap-1">
              <Chip tone="info" size="sm" className="min-w-0 shrink">
                {optionLabel(options, selected[0])}
              </Chip>
              {selected.length > 1 ? <span className="shrink-0 text-xs">+{selected.length - 1}</span> : null}
            </span>
          ) : widgetLabel(field, "Add record")}
        </SelectValue>
        <SelectIcon />
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Positioner sideOffset={4}>
          <SelectPrimitive.Content>
            <SelectList>
              {choices.map((option) => (
                <SelectItem key={option.value} value={option.value}
                  disabled={option.disabled && !selected.includes(option.value)}
                  label={optionTextLabel(option.label)}>
                  <SelectItemText>{option.label}</SelectItemText>
                  <SelectItemIndicator />
                </SelectItem>
              ))}
            </SelectList>
          </SelectPrimitive.Content>
        </SelectPrimitive.Positioner>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

function Many2ManyRead({
  value,
  field,
}: WidgetRenderProps<readonly unknown[]>): ReactElement {
  return (
    <Many2ManyChips
      values={normaliseValues(value)}
      options={field?.options ?? []}
    />
  );
}

function Many2ManyChips({
  values,
  options,
  onRemove,
}: {
  values: readonly string[];
  options: readonly WidgetOption[];
  onRemove?: (next: readonly string[]) => void;
}): ReactElement {
  if (values.length === 0) return <span className={textRoleVariants({ role: "meta" })} />;

  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
      {values.map((item) => {
        const label = optionLabel(options, item);
        return onRemove ? (
          <RemovableChip
            key={item}
            tone="info"
            size="sm"
            removeLabel={optionTextLabel(label, "record")}
            onRemove={() =>
              onRemove(values.filter((value) => value !== item))
            }
          >
            {label}
          </RemovableChip>
        ) : (
          <Chip key={item} tone="info" size="sm">
            {label}
          </Chip>
        );
      })}
    </span>
  );
}

export const many2manyWidget = {
  edit: Many2ManyEdit,
  read: Many2ManyRead,
  cell: Many2ManyRead,
} satisfies WidgetDefinition<readonly unknown[]>;

function normaliseValues(value: readonly unknown[] | null | undefined): string[] {
  return [...new Set((value ?? []).map(relationValueId))].filter(Boolean);
}
