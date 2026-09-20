import { Combobox as BaseCombobox } from "@base-ui/react/combobox";
import { useMemo, type ReactElement, type ReactNode } from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { PORTALED_CONTROL_LAYER } from "../ui/popover";
import { selectVariants } from "../ui/select";
import { textRoleVariants } from "../ui/text";
import { widgetControlSurface } from "../ui/widget-control";
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
  const styles = selectVariants();

  return (
    <BaseCombobox.Root
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
      <BaseCombobox.Trigger
        ref={controlRef}
        {...field?.controlProps}
        aria-label={widgetLabel(field, t("combobox.label"))}
        className={styles.trigger({
          className: cn(
            widgetControlSurface({
              focus: "visible",
              surface: "inset",
              readOnly,
              disabled: "data",
            }),
            "rounded-6",
          ),
        })}
      >
        <BaseCombobox.Value>
          {(selected) => (
            <span className={styles.value()}>
              {labels.get(String(selected ?? "")) ??
                widgetLabel(field, t("combobox.selectOption"))}
            </span>
          )}
        </BaseCombobox.Value>
        <BaseCombobox.Icon className={styles.icon()}>
          <Glyph name="chevron-down" />
        </BaseCombobox.Icon>
      </BaseCombobox.Trigger>
      <BaseCombobox.Portal>
        <BaseCombobox.Positioner className={PORTALED_CONTROL_LAYER} sideOffset={4}>
          <BaseCombobox.Popup className={styles.content()}>
            <label className="flex h-8 items-center gap-2 border-b border-border-subtle px-2 text-fg-muted">
              <Glyph name="search" className="shrink-0" />
              <BaseCombobox.Input
                aria-label={t("combobox.searchOptions")}
                className="min-w-0 flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted"
                placeholder={t("combobox.search")}
              />
            </label>
            <BaseCombobox.List className={styles.list()}>
              {(optionValue: string) => {
                const option = byValue.get(optionValue);
                if (!option) return null;
                return (
                  <BaseCombobox.Item
                    key={option.value}
                    value={option.value}
                    disabled={option.disabled}
                    className={styles.item()}
                  >
                    <span className={styles.itemText()}>{option.label}</span>
                    <BaseCombobox.ItemIndicator className={styles.indicator()}>
                      <Glyph name="check" />
                    </BaseCombobox.ItemIndicator>
                  </BaseCombobox.Item>
                );
              }}
            </BaseCombobox.List>
            <BaseCombobox.Empty
              className={cn(
                textRoleVariants({ role: "meta" }),
                "px-2 py-3",
              )}
            >
              {t("combobox.noOptions")}
            </BaseCombobox.Empty>
          </BaseCombobox.Popup>
        </BaseCombobox.Positioner>
      </BaseCombobox.Portal>
    </BaseCombobox.Root>
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
