import type { ReactElement } from "react";

import { Input } from "../ui/input";
import { TextLink } from "../ui/text-link";
import { widgetLabel } from "./label";
import type {
  WidgetDefinition,
  WidgetField,
  WidgetRenderProps,
} from "./types";

function EmailEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      type="email"
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Email")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
    />
  );
}

function EmailRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  return <span className="text-13 text-fg">{value ?? ""}</span>;
}

function UrlEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      type="url"
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "URL")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
    />
  );
}

function UrlRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  const label = value?.trim() ?? "";
  if (!label) return <span className="text-13 text-fg" />;
  return (
    <TextLink
      href={label}
      target="_blank"
      className="inline-block max-w-full truncate text-13"
    >
      {label}
    </TextLink>
  );
}

function PhoneEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      type="tel"
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Phone")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
    />
  );
}

function PhoneRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  return <span className="text-13 text-fg">{value ?? ""}</span>;
}

export const emailWidget = {
  edit: EmailEdit,
  read: EmailRead,
  cell: EmailRead,
} satisfies WidgetDefinition<string>;

export const urlWidget = {
  edit: UrlEdit,
  read: UrlRead,
  cell: UrlRead,
} satisfies WidgetDefinition<string>;

export const phoneWidget = {
  edit: PhoneEdit,
  read: PhoneRead,
  cell: PhoneRead,
} satisfies WidgetDefinition<string>;

function fieldPlaceholder(field: WidgetField | undefined): string | undefined {
  return typeof field?.label === "string" ? field.label : undefined;
}
