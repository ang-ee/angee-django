import type { ReactElement } from "react";

import type { WidgetDefinition, WidgetRenderProps } from "./types";

type UserRefRecord = {
  displayName?: unknown;
  name?: unknown;
  username?: unknown;
  email?: unknown;
  id?: unknown;
};

type UserRefValue = string | UserRefRecord;

function UserRefRead({
  value,
}: WidgetRenderProps<UserRefValue>): ReactElement {
  const label = userRefLabel(value);
  return (
    <span className="inline-flex min-w-0 items-center gap-2 text-13 text-fg">
      {label ? (
        <span className="grid size-5 shrink-0 place-content-center rounded-full bg-inset text-2xs font-semibold uppercase text-fg-muted">
          {initials(label)}
        </span>
      ) : null}
      <span className="min-w-0 truncate">{label}</span>
    </span>
  );
}

export const userRefWidget = {
  read: UserRefRead,
  cell: UserRefRead,
} satisfies WidgetDefinition<UserRefValue>;

function userRefLabel(value: UserRefValue | null | undefined): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  return (
    textValue(value.displayName) ??
    textValue(value.name) ??
    textValue(value.username) ??
    textValue(value.email) ??
    textValue(value.id) ??
    ""
  );
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function initials(label: string): string {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}
