import type { ReactElement } from "react";

import { Input } from "../ui/input";
import { widgetLabel } from "./label";
import type {
  WidgetDefinition,
  WidgetField,
  WidgetRenderProps,
} from "./types";

/**
 * Normalize text into a URL-safe slug: lowercased, with runs of non-alphanumerics
 * collapsed to a single hyphen and the edges trimmed. Used for the auto-derive from
 * a source field (e.g. a record's title) — see FormView's slug derivation.
 */
export function slugify(value: unknown): string {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// Keystroke normalization is lighter than `slugify`: it lowercases and turns invalid
// runs into hyphens but keeps a trailing hyphen, so a user can still type "foo-bar".
function slugifyInput(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9-]+/g, "-");
}

function SlugEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      value={value ?? ""}
      readOnly={readOnly}
      className="font-mono"
      aria-label={widgetLabel(field, field?.name ?? "slug")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(slugifyInput(event.currentTarget.value))}
    />
  );
}

function SlugRead({ value }: WidgetRenderProps<string>): ReactElement {
  return <span className="font-mono text-13 text-fg">{value ?? ""}</span>;
}

/**
 * Slug text field. Pairs with FormView's slug derivation: while creating, the value
 * tracks `slugify(<source field>)` (the record title by default, or the field named
 * by `slugFrom`) until the user edits it, then it holds.
 */
export const slugWidget = {
  edit: SlugEdit,
  read: SlugRead,
  cell: SlugRead,
} satisfies WidgetDefinition<string>;

function fieldPlaceholder(field: WidgetField | undefined): string | undefined {
  return typeof field?.label === "string" ? field.label : undefined;
}
