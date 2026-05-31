import type { ReactNode } from "react";

import type { ActionDescriptor, ActionProps } from "./Action";
import type { ColumnDescriptor, ColumnProps } from "./Column";
import type { FieldDescriptor, FieldProps } from "./Field";
import type { GroupDescriptor, GroupProps } from "./Group";
import { pageChildren, pageElementProps } from "./types";

export { Action, ACTION_SLOT, type ActionConfirm } from "./Action";
export {
  Column,
  COLUMN_SLOT,
  type ColumnAggregate,
  type PageColumnAlign,
} from "./Column";
export { Field, FIELD_SLOT, type PageFieldKind } from "./Field";
export { Group, GROUP_SLOT } from "./Group";
export {
  PAGE_ELEMENT_SLOT,
  type PageElement,
  type PageElementKind,
} from "./types";
export type {
  ActionDescriptor,
  ActionProps,
  ColumnDescriptor,
  ColumnProps,
  FieldDescriptor,
  FieldProps,
  GroupDescriptor,
  GroupProps,
};

export function parsePageColumns<
  TRow extends object = Record<string, unknown>,
>(children: ReactNode): ColumnDescriptor<TRow>[] {
  return pageChildren(children).flatMap((child) => {
    const props = pageElementProps<ColumnProps<TRow>>(child, "column");
    return props ? [columnDescriptor(props)] : [];
  });
}

export function parsePageFields(children: ReactNode): FieldDescriptor[] {
  const fields: FieldDescriptor[] = [];
  for (const child of pageChildren(children)) {
    const field = pageElementProps<FieldProps>(child, "field");
    if (field) {
      fields.push(fieldDescriptor(field));
      continue;
    }
    const group = pageElementProps<GroupProps>(child, "group");
    if (group) fields.push(...parsePageFields(group.children));
  }
  return fields;
}

export function parsePageGroups(children: ReactNode): GroupDescriptor[] {
  return pageChildren(children).flatMap((child) => {
    const props = pageElementProps<GroupProps>(child, "group");
    return props ? [groupDescriptor(props)] : [];
  });
}

export function parsePageActions(children: ReactNode): ActionDescriptor[] {
  return pageChildren(children).flatMap((child) => {
    const props = pageElementProps<ActionProps>(child, "action");
    return props ? [actionDescriptor(props)] : [];
  });
}

function columnDescriptor<
  TRow extends object = Record<string, unknown>,
>(props: ColumnProps<TRow>): ColumnDescriptor<TRow> {
  return {
    field: props.field,
    ...(props.header !== undefined ? { header: props.header } : {}),
    ...(props.widget !== undefined ? { widget: props.widget } : {}),
    ...(props.sortable !== undefined ? { sortable: props.sortable } : {}),
    ...(props.aggregate !== undefined ? { aggregate: props.aggregate } : {}),
    ...(props.align !== undefined ? { align: props.align } : {}),
    ...(props.render !== undefined ? { render: props.render } : {}),
  };
}

function fieldDescriptor(props: FieldProps): FieldDescriptor {
  return {
    name: props.name,
    ...(props.label !== undefined ? { label: props.label } : {}),
    ...(props.widget !== undefined ? { widget: props.widget } : {}),
    ...(props.readOnly !== undefined ? { readOnly: props.readOnly } : {}),
    ...(props.title !== undefined ? { title: props.title } : {}),
    ...(props.kind !== undefined ? { kind: props.kind } : {}),
  };
}

function actionDescriptor(props: ActionProps): ActionDescriptor {
  return {
    id: props.id,
    label: props.label,
    ...(props.disabled !== undefined ? { disabled: props.disabled } : {}),
    ...(props.danger !== undefined ? { danger: props.danger } : {}),
    ...(props.confirm !== undefined ? { confirm: props.confirm } : {}),
    ...(props.onClick !== undefined ? { onClick: props.onClick } : {}),
  };
}

function groupDescriptor(props: GroupProps): GroupDescriptor {
  return {
    ...(props.label !== undefined ? { label: props.label } : {}),
    ...(props.columns !== undefined ? { columns: props.columns } : {}),
    fields: parseDirectPageFields(props.children),
    actions: parsePageActions(props.children),
  };
}

function parseDirectPageFields(children: ReactNode): FieldDescriptor[] {
  return pageChildren(children).flatMap((child) => {
    const props = pageElementProps<FieldProps>(child, "field");
    return props ? [fieldDescriptor(props)] : [];
  });
}
