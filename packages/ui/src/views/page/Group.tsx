import type { ReactNode } from "react";

import type { ActionDescriptor } from "./Action";
import type { FieldDescriptor } from "./Field";
import { PAGE_ELEMENT_SLOT } from "./types";

export interface GroupProps {
  label?: ReactNode;
  columns?: number;
  children?: ReactNode;
}

export interface GroupDescriptor {
  label?: ReactNode;
  columns?: number;
  fields: readonly FieldDescriptor[];
  actions: readonly ActionDescriptor[];
}

function GroupMarker(_props: GroupProps): null {
  return null;
}

export const Group = Object.assign(GroupMarker, {
  [PAGE_ELEMENT_SLOT]: "group" as const,
});
