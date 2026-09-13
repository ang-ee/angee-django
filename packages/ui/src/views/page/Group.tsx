import type { ReactNode } from "react";

import type { ActionDescriptor } from "./Action";
import type { FieldDescriptor } from "./Field";
import { PAGE_ELEMENT_SLOT } from "./types";

/** Which column of a `layout="sidebar"` form a group belongs to. */
export type GroupPlacement = "main" | "properties";

export interface GroupProps {
  label?: ReactNode;
  columns?: number;
  collapsible?: boolean;
  defaultOpen?: boolean;
  /**
   * Where the group sits under `layout="sidebar"`: `properties` moves it to the
   * standing right-hand column, the rest stay in the main body. Ignored by the
   * other layouts, so a donor addon can mark its group once and not care which
   * layout the host form chose.
   */
  placement?: GroupPlacement;
  children?: ReactNode;
}

export interface GroupDescriptor {
  label?: ReactNode;
  columns?: number;
  collapsible?: boolean;
  defaultOpen?: boolean;
  placement?: GroupPlacement;
  fields: readonly FieldDescriptor[];
  actions: readonly ActionDescriptor[];
}

function GroupMarker(_props: GroupProps): null {
  return null;
}

export const Group = Object.assign(GroupMarker, {
  [PAGE_ELEMENT_SLOT]: "group" as const,
});
