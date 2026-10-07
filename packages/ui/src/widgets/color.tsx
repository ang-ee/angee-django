import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import { Input } from "../ui/input";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

/** A `#rrggbb` colour, the only form the native picker and `ColorField` exchange. */
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

function Swatch({ value, className }: { value: string | null | undefined; className?: string }): ReactElement {
  const hex = typeof value === "string" && HEX_COLOR.test(value) ? value : undefined;
  return (
    <span
      role="img"
      aria-label={hex ?? "No colour"}
      className={cn("inline-block size-4 shrink-0 rounded-full border border-border", !hex && "border-dashed", className)}
      style={hex ? { backgroundColor: hex } : undefined}
    />
  );
}

/**
 * The colour chooser: the native picker drawn as a swatch beside the hex text, both
 * writing the same `#rrggbb` value. Backend `ColorField` columns classify to this
 * widget, so owners place `<Field name="color" />` and never pick it by hand.
 */
function ColorEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  const hex = typeof value === "string" && HEX_COLOR.test(value) ? value : "#000000";
  const label = widgetLabel(field, "Colour");
  return (
    <span className="inline-flex w-full items-center gap-2">
      <input
        type="color"
        value={hex}
        disabled={readOnly}
        aria-label={`${label} picker`}
        className="size-7 shrink-0 cursor-pointer rounded-full border border-border bg-transparent p-0 disabled:cursor-default [&::-webkit-color-swatch]:rounded-full [&::-webkit-color-swatch]:border-0 [&::-webkit-color-swatch-wrapper]:p-0.5"
        onChange={(event) => onChange?.(event.currentTarget.value)}
        onBlur={onCommit}
      />
      <Input
        {...field?.controlProps}
        ref={controlRef}
        value={value ?? ""}
        readOnly={readOnly}
        aria-label={label}
        placeholder="#rrggbb"
        maxLength={7}
        spellCheck={false}
        onChange={(event) => onChange?.(event.currentTarget.value)}
        onBlur={onCommit}
      />
    </span>
  );
}

function ColorRead({ value }: WidgetRenderProps<string>): ReactElement {
  return (
    <span className="inline-flex items-center gap-1.5 text-13 text-fg">
      <Swatch value={value} />
      <span className={cn(!value && "text-fg-muted")}>{value || "—"}</span>
    </span>
  );
}

export const colorWidget = {
  edit: ColorEdit,
  read: ColorRead,
  cell: ColorRead,
} satisfies WidgetDefinition<string>;
