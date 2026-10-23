import { useMemo, type ReactElement, type ReactNode } from "react";

import { useUiT } from "../i18n";
import { Combobox } from "../ui/combobox";
import { widgetControlPresentationProps } from "../ui/widget-control";
import { widgetLabel } from "./label";
import {
  canonicalOptionValue,
  optionLabel,
  optionTextLabel,
  type WidgetDefinition,
  type WidgetOption,
  type WidgetControlProps,
  type WidgetRenderProps,
} from "./types";

function ComboboxEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  const t = useUiT();
  const options = field?.options ?? [];
  const labels = useMemo(() => optionLabelMap(options), [options]);
  const byValue = useMemo(
    () => new Map(options.map((option) => [option.value, option])),
    [options],
  );
  const values = useMemo(() => options.map((option) => option.value), [options]);
  const selected = canonicalOptionValue(options, value) ?? null;
  const { presentation, ...controlProps } = field?.controlProps ?? ({} as Partial<WidgetControlProps>);
  const sizing = widgetControlPresentationProps(presentation);
  const invalid = controlProps["aria-invalid"] === true;

  return (
    <Combobox.Root
      items={values}
      value={selected}
      readOnly={readOnly}
      disabled={readOnly}
      onValueChange={(next) => {
        onChange?.(next ?? "");
        onCommit?.();
      }}
      filter={(optionValue, query) =>
        optionMatches(optionValue, labels.get(optionValue), query)
      }
    >
      <Combobox.Trigger
        ref={controlRef}
        {...controlProps}
        aria-label={widgetLabel(field, t("combobox.label"))}
        presentation={presentation}
        invalid={invalid}
        readOnly={readOnly}
      >
        <Combobox.Value>
          {(selected) =>
            labels.get(String(selected ?? "")) ?? widgetLabel(field, t("combobox.selectOption"))}
        </Combobox.Value>
        <Combobox.Icon />
      </Combobox.Trigger>
      <Combobox.Popup size={sizing.size}>
        <Combobox.Search />
        <Combobox.List>
          {(optionValue: string) => {
            const option = byValue.get(optionValue);
            if (!option) return null;
            return (
              <Combobox.Item key={option.value} value={option.value} disabled={option.disabled} size={sizing.size}>
                <Combobox.ItemText>{option.label}</Combobox.ItemText>
                <Combobox.ItemIndicator />
              </Combobox.Item>
            );
          }}
        </Combobox.List>
        <Combobox.Empty />
      </Combobox.Popup>
    </Combobox.Root>
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

function optionMatches(value: string, label: ReactNode, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  const haystack = `${value} ${optionTextLabel(label, "")}`;
  return haystack.toLowerCase().includes(normalized);
}
