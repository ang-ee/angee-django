import * as React from "react";
import { holdsPermission, type ModelMetadata, type Row } from "@angee/metadata";

import type { ComposedContainerChild } from "../../runtime";
import { pageChildren, type FieldDescriptor } from "../page";

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

/** Declaration consumed from the record rail container (`form#rail`, `<model>#rail`). */
export function RecordRailGroup(_props: RecordRailGroupProps): null {
  return null;
}

export function recordRailGroups(entries: readonly ComposedContainerChild[]): readonly RecordRailGroupProps[] {
  // A child's permission gates every group it declares, together with the group's own.
  const groups = entries.flatMap((entry) => pageChildren(entry.content as React.ReactNode).flatMap((child) =>
    React.isValidElement<RecordRailGroupProps>(child) && child.type === RecordRailGroup
      ? [entry.permission === undefined ? child.props : { ...child.props, childPermission: entry.permission }]
      : [],
  ));
  const seen = new Set<string>();
  for (const group of groups) {
    if (seen.has(group.id)) throw new Error(`FormView received duplicate record rail group id "${group.id}".`);
    seen.add(group.id);
  }
  return groups;
}

/** Only the server-authorized, projected rows reach the record rail. */
export function visibleRecordRailGroups(
  groups: readonly RecordRailGroupProps[],
  record: Row | null,
  metadata: ModelMetadata | null,
): readonly RecordRailGroupProps[] {
  if (!record) return [];
  return groups.flatMap((group) => {
    if (group.permission && !holdsPermission(record, group.permission)) return [];
    if (group.childPermission && !holdsPermission(record, group.childPermission)) return [];
    const fields = (group.fields ?? []).filter(({ field, permission }) =>
      !field.hidden
      && (!permission || holdsPermission(record, permission))
      && Boolean(metadata?.fields[field.name])
      && metadata?.fields[field.name]?.readable !== false,
    );
    return fields.length > 0 || group.content != null ? [{ ...group, fields }] : [];
  });
}
