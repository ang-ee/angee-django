import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { Chip, ChipList } from "../ui/chip";
import { Select, SelectPrimitive, SelectValue, SelectIcon, SelectList, SelectItem, SelectItemText, SelectItemIndicator } from "../ui/select";
import { widgetLabel } from "./label";
import {
  optionLabel,
  optionTextLabel,
  relationIdList,
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
  const t = useUiT();
  if (field?.controlProps?.presentation === "cell") {
    return <Many2ManyCellEdit value={value} onChange={onChange} field={field} readOnly={readOnly} controlRef={controlRef} />;
  }
  const selected = relationIdList(value);
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
        {...field?.controlProps}
        triggerRef={controlRef}
        value=""
        options={available}
        disabled={available.length === 0}
        aria-label={widgetLabel(field, t("many2many.label"))}
        placeholder={
          available.length === 0
            ? t("many2many.allSelected")
            : widgetLabel(field, t("many2many.add"))
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
  controlRef,
}: WidgetRenderProps<readonly unknown[]>): ReactElement {
  const t = useUiT();
  const selected = relationIdList(value);
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
        ref={controlRef}
        {...field?.controlProps}
        disabled={choices.length === 0}
        aria-label={widgetLabel(field, t("many2many.label"))}
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
          ) : widgetLabel(field, t("many2many.add"))}
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
      values={relationIdList(value)}
      options={field?.options ?? []}
    />
  );
}

/** Selected ids as chips labelled by their options; removable when editing. */
function Many2ManyChips({
  values,
  options,
  onRemove,
}: {
  values: readonly string[];
  options: readonly WidgetOption[];
  onRemove?: (next: readonly string[]) => void;
}): ReactElement {
  const t = useUiT();
  return (
    <ChipList
      items={values.map((id) => {
        const label = optionLabel(options, id);
        return { id, label, text: optionTextLabel(label, t("many2many.record")) };
      })}
      onRemove={onRemove && ((id) => onRemove(values.filter((value) => value !== id)))}
    />
  );
}

export const many2manyWidget = {
  edit: Many2ManyEdit,
  read: Many2ManyRead,
  cell: Many2ManyRead,
} satisfies WidgetDefinition<readonly unknown[]>;
