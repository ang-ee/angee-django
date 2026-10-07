import { Combobox } from "@base-ui/react/combobox";
import { useRef, useState, type ReactElement } from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { textRoleVariants } from "../ui/text";
import { Chip, ChipList, RemovableChip } from "../ui/chip";
import { chipInputVariants } from "../ui/input";
import { PORTALED_CONTROL_LAYER } from "../ui/popover";
import { SelectPrimitive, SelectValue, SelectIcon, SelectList, SelectItem, SelectItemText, SelectItemIndicator, selectVariants } from "../ui/select";
import { widgetControlPresentationProps } from "../ui/widget-control";
import { widgetLabel } from "./label";
import {
  optionLabel,
  optionTextLabel,
  relationIdList,
  type WidgetControlProps,
  type WidgetDefinition,
  type WidgetOption,
  type WidgetRenderProps,
} from "./types";

/** The picker item that offers to create a record named by the typed query. */
const CREATE_ITEM = "\u0000create";

export interface Many2ManyEditProps extends WidgetRenderProps<readonly unknown[]> {
  /** Offers "Create “query”" as the last option for a typed query no option matches. */
  onCreate?: (query: string) => void;
  /** The typed query, for an owner that searches the options server-side. */
  onSearchChange?: (query: string) => void;
}

export function Many2ManyEdit({
  value,
  onChange,
  field,
  readOnly,
  controlRef,
  onCreate,
  onSearchChange,
}: Many2ManyEditProps): ReactElement {
  if (field?.controlProps?.presentation === "cell") {
    return <Many2ManyCellEdit value={value} onChange={onChange} field={field} readOnly={readOnly} controlRef={controlRef} />;
  }
  if (readOnly) return <Many2ManyRead value={relationIdList(value)} field={field} />;
  return <Many2ManyChipsEdit value={value} onChange={onChange} field={field} controlRef={controlRef}
    onCreate={onCreate} onSearchChange={onSearchChange} />;
}

/**
 * One field-shaped control: the picked records as removable chips, then an
 * inline search that adds the picked option as a chip, closing with
 * "Create “query”" when the owner can create. Base UI owns chip focus,
 * Backspace removal and keyboard navigation.
 */
function Many2ManyChipsEdit({
  value,
  onChange,
  field,
  controlRef,
  onCreate,
  onSearchChange,
}: Many2ManyEditProps): ReactElement {
  const t = useUiT();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  // The options drop beneath the whole field box, at its width, not beneath the inline input.
  const boxRef = useRef<HTMLDivElement>(null);
  const selected = relationIdList(value);
  const options = field?.options ?? [];
  const { presentation, ...controlProps } = field?.controlProps ?? ({} as Partial<WidgetControlProps>);
  const sizing = widgetControlPresentationProps(presentation);
  const styles = selectVariants(sizing);
  const text = query.trim();
  const needle = text.toLocaleLowerCase();
  const labelText = (option: WidgetOption) => optionTextLabel(option.label, option.value);
  // An owner that searches server-side hands over the matching page; otherwise match loaded labels.
  const matches = options.filter((option) => !selected.includes(option.value) && !option.disabled
    && (onSearchChange !== undefined || !needle || labelText(option).toLocaleLowerCase().includes(needle)));
  const exact = options.some((option) => labelText(option).trim().toLocaleLowerCase() === needle);
  const offered = [...matches.map((option) => option.value), ...(onCreate && text && !exact ? [CREATE_ITEM] : [])];
  // Nothing left to pick and nothing typed opens no list: an empty "No options" panel is a dead end.
  const shown = open && (offered.length > 0 || text !== "");
  const search = (next: string) => {
    setQuery(next);
    onSearchChange?.(next);
  };
  const remove = (id: string) => onChange?.(selected.filter((item) => item !== id));
  return (
    <Combobox.Root<string, true> multiple autoHighlight
      value={selected} items={[...selected, ...offered]} filteredItems={offered} filter={null}
      open={shown} onOpenChange={setOpen}
      inputValue={query} onInputValueChange={search}
      itemToStringLabel={(item) => item === CREATE_ITEM ? text : optionTextLabel(optionLabel(options, item), item)}
      onValueChange={(next, details) => {
        // Escape dismisses the search; it never clears the field.
        if (details.reason === "escape-key") { details.allowPropagation(); return; }
        if (next.includes(CREATE_ITEM)) onCreate?.(text);
        else onChange?.(next);
        search("");
        // A pick closes the list: the box may wrap onto another row, and the list reopens beneath it.
        setOpen(false);
      }}>
      <Combobox.Chips ref={boxRef} className={chipInputVariants({ ...sizing, focus: "within", invalid: Boolean(controlProps["aria-invalid"]) })}>
        {selected.map((id) => {
          const label = optionLabel(options, id);
          return <Combobox.Chip key={id} render={<RemovableChip tone="info" size="sm"
            removeLabel={optionTextLabel(label, t("many2many.record"))} onRemove={() => remove(id)} />}>
            {label}
          </Combobox.Chip>;
        })}
        <Combobox.Input {...controlProps} ref={controlRef} aria-label={widgetLabel(field, t("many2many.label"))}
          className="h-5 min-w-[7rem] flex-1 border-0 bg-transparent text-13 text-fg outline-none" />
      </Combobox.Chips>
      <Combobox.Portal>
        <Combobox.Positioner anchor={boxRef} className={PORTALED_CONTROL_LAYER} sideOffset={4}>
          <Combobox.Popup className={styles.content()}>
            <Combobox.List className={styles.list()}>
              {(item: string) => <Combobox.Item key={item} value={item} className={styles.item()}>
                <span className={styles.itemText()}>
                  {item === CREATE_ITEM ? t("relation.create", { query: text }) : optionLabel(options, item)}
                </span>
              </Combobox.Item>}
            </Combobox.List>
            <Combobox.Empty className={cn(textRoleVariants({ role: "meta" }), "px-2 py-3 empty:hidden")}>
              {t("combobox.noOptions")}
            </Combobox.Empty>
          </Combobox.Popup>
        </Combobox.Positioner>
      </Combobox.Portal>
    </Combobox.Root>
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
