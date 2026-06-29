import type { ReactElement } from "react";

import { avatarInitials } from "../ui/avatar";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type UserRefValue = string;

function UserRefRead({
  value,
}: WidgetRenderProps<UserRefValue>): ReactElement {
  const label = value?.trim() ?? "";
  return (
    <span className="inline-flex min-w-0 items-center gap-2 text-13 text-fg">
      {label ? (
        <span className="grid size-5 shrink-0 place-content-center rounded-full bg-inset text-2xs font-semibold uppercase text-fg-muted">
          {avatarInitials(label)}
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
