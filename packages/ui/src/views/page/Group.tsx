import type { ReactNode } from "react";

import type { ActionDescriptor } from "./Action";
import type { FieldDescriptor } from "./Field";
import { PAGE_ELEMENT_SLOT } from "./types";

export interface GroupProps {
  label?: ReactNode;
  /** One sentence beside the label saying what the group holds or where it comes from. */
  hint?: ReactNode;
  /** Who reads this group, shown at the heading's right. */
  audience?: ReactNode;
  columns?: number;
  collapsible?: boolean;
  defaultOpen?: boolean;
  /** Saved-record content following this group's declared fields. */
  content?: ReactNode;
  /** Shown only on a saved record: a create form omits the group and its fields. */
  savedOnly?: boolean;
  /**
   * This section holds the form's editable lines. Declared, the lines render here,
   * titled by `label`, and only while this group is shown, so a `#sections` child
   * carrying it is narrowed like any section; undeclared, they trail the form.
   */
  lines?: boolean;
  /**
   * The group is its own pane beneath the sheet, with this id, titled by `label`,
   * instead of a sheet section. Its fields stay bound to the record's form.
   */
  pane?: string;
  children?: ReactNode;
}

export interface GroupDescriptor {
  label?: ReactNode;
  hint?: ReactNode;
  audience?: ReactNode;
  columns?: number;
  collapsible?: boolean;
  defaultOpen?: boolean;
  fields: readonly FieldDescriptor[];
  actions: readonly ActionDescriptor[];
  content?: ReactNode;
  savedOnly?: boolean;
  /** The section holds the form's editable lines. */
  lines?: boolean;
  /** The section is the pane with this id rather than part of the sheet. */
  pane?: string;
  /** Projected record permission required to show this group; set from a contribution. */
  permission?: string;
  /** The container child that contributed it, when the record decides (its `impl` or a variant); set by FormView. */
  containerChild?: string;
}

function GroupMarker(_props: GroupProps): null {
  return null;
}

export const Group = Object.assign(GroupMarker, {
  [PAGE_ELEMENT_SLOT]: "group" as const,
});
