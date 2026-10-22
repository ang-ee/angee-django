import type { ReactElement } from "react";

import { Avatar, avatarInitials } from "../ui/avatar";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type UserRefValue = string;

function UserRefRead({
  value,
}: WidgetRenderProps<UserRefValue>): ReactElement {
  const label = value?.trim() ?? "";
  return (
    <span className="inline-flex min-w-0 items-center gap-2 text-13 text-fg">
      {label ? <Avatar size="sm" alt={label} initials={avatarInitials(label)} className="shrink-0" /> : null}
      <span className="min-w-0 truncate">{label}</span>
    </span>
  );
}

export const userRefWidget = {
  read: UserRefRead,
  cell: UserRefRead,
} satisfies WidgetDefinition<UserRefValue>;
