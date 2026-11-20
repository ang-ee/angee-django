import * as React from "react";
import { holdsPermission, type ModelMetadata, type Row } from "@angee/metadata";

import type { ComposedContainerChild } from "../../runtime";
import { pageChildren, type FieldDescriptor } from "../page";
import { rowDependentChildren } from "./container-admission";
import { isFieldVisible, resolveField, type FormValues } from "./form-view-model";

export interface RecordRailField {
  field: FieldDescriptor;
  /** The server-projected permission required to read this row. */
  permission?: string;
}

export interface RecordRailGroupProps {
  id: string;
  label: React.ReactNode;
  summary?: React.ReactNode;
  hint?: React.ReactNode;
  audience?: React.ReactNode;
  /** The server-projected permission required to read this group. */
  permission?: string;
  fields?: readonly RecordRailField[];
  /** A capability owner may place a richer panel after its field rows. */
  content?: React.ReactNode;
  /** The contributing child's permission, required alongside the group's own. */
  childPermission?: string;
}

/** A rail group as FormView holds it: the declaration, tagged with its child when the record decides that child. */
export interface ContributedRailGroup extends RecordRailGroupProps {
  /** The contributing child, an `impl` child, a variant or its original, shown on the records that admit it. */
  containerChild?: string;
}

/** Declaration consumed from the record rail container (`form#rail`, `<model>#rail`).
 * @kind contribution
 */
export function RecordRailGroup(_props: RecordRailGroupProps): null {
  return null;
}

export function recordRailGroups(entries: readonly ComposedContainerChild[]): readonly ContributedRailGroup[] {
  // A child's permission gates every group it declares, together with the group's own;
  // a child the record decides tags its groups, which show on the records that admit it.
  const rowDependent = rowDependentChildren(entries);
  const groups = entries.flatMap((entry) => pageChildren(entry.content as React.ReactNode).flatMap((child) =>
    React.isValidElement<RecordRailGroupProps>(child) && child.type === RecordRailGroup
      ? [{
          ...child.props,
          ...(entry.permission !== undefined ? { childPermission: entry.permission } : {}),
          ...(rowDependent.has(entry.id) ? { containerChild: entry.id } : {}),
        }]
      : [],
  ));
  // Alternatives the record chooses between (an original and its variants, impl children) may share
  // a group id among themselves, never with a group every record shows.
  const fixed = groups.filter((group) => group.containerChild === undefined);
  assertUniqueRailGroups(fixed);
  const fixedIds = new Set(fixed.map((group) => group.id));
  const clash = groups.find((group) => group.containerChild !== undefined && fixedIds.has(group.id));
  if (clash) throw new Error(`FormView received duplicate record rail group id "${clash.id}".`);
  return groups;
}

function assertUniqueRailGroups(groups: readonly RecordRailGroupProps[]): void {
  const seen = new Set<string>();
  for (const group of groups) {
    if (seen.has(group.id)) throw new Error(`FormView received duplicate record rail group id "${group.id}".`);
    seen.add(group.id);
  }
}

/**
 * Only the server-authorized, projected rows reach the record rail, from the children the record
 * admits; of those, a field shows as the sheet's do: visible for the form's `values` (its
 * `showWhen`, resolved through `resolve`) and not `hidden`. Where the sheet's own declared fields
 * (`declared`) hold one of the row's name, that declaration decides whether the row is visible,
 * through its `showWhen` and `resolve`; the row's own descriptor decides only how it presents (its
 * label, its widget, its own `hidden`). A row the sheet does not declare decides both itself: one
 * rail row never decides another's. A group left with no field and no content is dropped.
 */
export function visibleRecordRailGroups(
  groups: readonly ContributedRailGroup[],
  record: Row | null,
  values: FormValues,
  metadata: ModelMetadata | null,
  admits: (child: string, record: Row) => boolean = () => true,
  declared: readonly FieldDescriptor[] = [],
): readonly ContributedRailGroup[] {
  if (!record) return [];
  const admitted = groups.filter((group) => group.containerChild === undefined || admits(group.containerChild, record));
  // Two alternatives a record admits together (two impl children for its impl) still clash.
  assertUniqueRailGroups(admitted);
  return admitted.flatMap((group) => {
    if (group.permission && !holdsPermission(record, group.permission)) return [];
    if (group.childPermission && !holdsPermission(record, group.childPermission)) return [];
    const fields = (group.fields ?? []).flatMap((row) => {
      if (row.permission && !holdsPermission(record, row.permission)) return [];
      if (!metadata?.fields[row.field.name] || metadata.fields[row.field.name]?.readable === false) return [];
      const field = resolveField(row.field, values);
      const owner = declared.find((candidate) => candidate.name === row.field.name);
      return isFieldVisible(owner ? resolveField(owner, values) : field, values) && !field.hidden ? [{ ...row, field }] : [];
    });
    return fields.length > 0 || group.content != null ? [{ ...group, fields }] : [];
  });
}
