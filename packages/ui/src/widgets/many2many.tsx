import type { ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { Select } from "../ui/select";
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

function Many2ManyEdit({
  value,
  onChange,
  field,
  readOnly,
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
        return (
          <Chip key={item} tone="info" size="sm" className="gap-1 pr-1">
            <span className="min-w-0 truncate">{label}</span>
            {onRemove ? (
              <Button
                type="button"
                variant="ghost"
                size="iconSm"
                className="size-4 rounded-full"
                aria-label={`Remove ${optionTextLabel(label, "record")}`}
                onClick={() =>
                  onRemove(values.filter((value) => value !== item))
                }
              >
                <Glyph name="x" />
              </Button>
            ) : null}
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
