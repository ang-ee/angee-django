import type { ReactElement } from "react";

import { Input } from "../ui/input";
import { NavLink } from "../ui/nav-link";
import { widgetLabel } from "./label";
import type {
  WidgetDefinition,
  WidgetField,
  WidgetRenderProps,
} from "./types";

function EmailEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      ref={controlRef}
      {...field?.controlProps}
      type="email"
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Email")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
      onBlur={onCommit}
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
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      ref={controlRef}
      {...field?.controlProps}
      type="url"
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "URL")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
      onBlur={onCommit}
    />
  );
}

function UrlRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  const label = value?.trim() ?? "";
  if (!label) return <span className="text-13 text-fg" />;
  return (
    <NavLink
      href={label}
      target="_blank"
      variant="inline"
      className="inline-block max-w-full truncate text-13"
    >
      {label}
    </NavLink>
  );
}

function PhoneEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      ref={controlRef}
      {...field?.controlProps}
      type="tel"
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Phone")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
      onBlur={onCommit}
    />
  );
}

function PhoneRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  return <span className="text-13 text-fg">{value ?? ""}</span>;
}

function PasswordEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      ref={controlRef}
      {...field?.controlProps}
      type="password"
      autoComplete="new-password"
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Password")}
      placeholder={fieldPlaceholder(field)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
      onBlur={onCommit}
    />
  );
}

function PasswordRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  return <span className="text-13 text-fg">{value ? "••••••••" : ""}</span>;
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

/**
 * The masked entry of a secret that is not the person's own sign-in password: a
 * vendor key, a service's password, another account's password. Its
 * autocomplete hint is `new-password`, the standard one that keeps a browser from
 * filling the person's saved sign-in into it; the sign-in form keeps its own
 * `current-password` hint. The read form masks a value and shows nothing for none.
 */
export const passwordWidget = {
  edit: PasswordEdit,
  read: PasswordRead,
} satisfies WidgetDefinition<string>;

function fieldPlaceholder(field: WidgetField | undefined): string | undefined {
  return field?.placeholder ?? (typeof field?.label === "string" ? field.label : undefined);
}
