import type { ReactNode } from "react";

import type {
  ActionContext,
  ActionDescriptor,
  ActionProps,
  ActionResult,
} from "./Action";
import type { ColumnDescriptor, ColumnProps } from "./Column";
import type { FacetDescriptor, FacetProps } from "./Facet";
import type { FieldDescriptor, FieldProps } from "./Field";
import type { GroupDescriptor, GroupProps } from "./Group";
import type { TabDescriptor, TabProps } from "./Tab";
import {
  pageChildren,
  pageChildrenCacheKey,
  pageElementProps,
} from "./types";

export { Action, type ActionConfirm } from "./Action";
export {
  Column,
  columnTone,
  type ColumnAggregate,
  type PageColumnAlign,
} from "./Column";
export { Facet } from "./Facet";
export {
  Field,
  fieldWidgetId,
  isRelationIdField,
  type PageFieldKind,
} from "./Field";
export { Group } from "./Group";
export { Tab } from "./Tab";
export {
  PAGE_ELEMENT_SLOT,
  pageChildren,
  pageElementProps,
  type PageElement,
  type PageElementKind,
} from "./types";
export type {
  ActionContext,
  ActionDescriptor,
  ActionProps,
  ActionResult,
  ColumnDescriptor,
  ColumnProps,
  FacetDescriptor,
  FacetProps,
  FieldDescriptor,
  FieldProps,
  GroupDescriptor,
  GroupProps,
  TabDescriptor,
  TabProps,
};

export function parsePageColumns<
  TRow extends object = Record<string, unknown>,
>(children: ReactNode): ColumnDescriptor<TRow>[] {
  return cachedChildDescriptors(
    columnListCache,
    children,
    () => {
      const columns = pageChildren(children).flatMap((child) => {
        const props = pageElementProps<ColumnProps<TRow>>(child, "column");
        return props ? [columnDescriptor(props)] : [];
      });
      return assertUniqueDescriptor(
        columns,
        (column) => column.field,
        "column field",
      );
    },
  );
}

export function parsePageFields(children: ReactNode): FieldDescriptor[] {
  return cachedChildDescriptors(fieldListCache, children, () => {
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
    return assertUniqueDescriptor(fields, (field) => field.name, "field name");
  });
}

export function parsePageGroups(children: ReactNode): GroupDescriptor[] {
  return cachedChildDescriptors(groupListCache, children, () =>
    pageChildren(children).flatMap((child) => {
      const props = pageElementProps<GroupProps>(child, "group");
      return props ? [groupDescriptor(props)] : [];
    }),
  );
}

export function parsePageActions(children: ReactNode): ActionDescriptor[] {
  return cachedChildDescriptors(actionListCache, children, () => {
    const actions = pageChildren(children).flatMap((child) => {
      const props = pageElementProps<ActionProps>(child, "action");
      return props ? [actionDescriptor(props)] : [];
    });
    return assertUniqueDescriptor(actions, (action) => action.id, "action id");
  });
}

export function parsePageTabs(children: ReactNode): TabDescriptor[] {
  return cachedChildDescriptors(tabListCache, children, () => {
    const tabs = pageChildren(children).flatMap((child) => {
      const props = pageElementProps<TabProps>(child, "tab");
      return props ? [tabDescriptor(props)] : [];
    });
    return assertUniqueDescriptor(tabs, (tab) => tab.id, "tab id");
  });
}

export function requirePageResource(
  component: string,
  resource: string | undefined,
): string {
  if (resource) return resource;
  throw new Error(`${component} requires a resource when rendered standalone.`);
}

export function requirePageColumns<
  TRow extends object = Record<string, unknown>,
>(
  component: string,
  columns: readonly ColumnDescriptor<TRow>[],
): readonly ColumnDescriptor<TRow>[] {
  if (columns.length > 0) return columns;
  throw new Error(`${component} requires at least one Column child.`);
}

function columnDescriptor<
  TRow extends object = Record<string, unknown>,
>(props: ColumnProps<TRow>): ColumnDescriptor<TRow> {
  return cachedDescriptor(columnDescriptorCache, props, () => ({
    field: props.field,
    ...(props.header !== undefined ? { header: props.header } : {}),
    ...(props.widget !== undefined ? { widget: props.widget } : {}),
    ...(props.options !== undefined ? { options: props.options } : {}),
    ...(props.sortable !== undefined ? { sortable: props.sortable } : {}),
    ...(props.aggregate !== undefined ? { aggregate: props.aggregate } : {}),
    ...(props.align !== undefined ? { align: props.align } : {}),
    ...(props.render !== undefined ? { render: props.render } : {}),
    ...(props.tone !== undefined ? { tone: props.tone } : {}),
  }));
}

function facetDescriptor(props: FacetProps): FacetDescriptor {
  return cachedDescriptor(facetDescriptorCache, props, () => ({
    field: props.field,
    ...(props.label !== undefined ? { label: props.label } : {}),
    ...(props.filterField !== undefined
      ? { filterField: props.filterField }
      : {}),
    ...(props.filterMode !== undefined ? { filterMode: props.filterMode } : {}),
    ...(props.aggregateKey !== undefined
      ? { aggregateKey: props.aggregateKey }
      : {}),
    ...(props.labelField !== undefined ? { labelField: props.labelField } : {}),
    ...(props.pageSize !== undefined ? { pageSize: props.pageSize } : {}),
    ...(props.group !== undefined ? { group: props.group } : {}),
  }));
}

function fieldDescriptor(props: FieldProps): FieldDescriptor {
  return cachedDescriptor(fieldDescriptorCache, props, () => ({
    name: props.name,
    ...(props.label !== undefined ? { label: props.label } : {}),
    ...(props.widget !== undefined ? { widget: props.widget } : {}),
    ...(props.readOnly !== undefined ? { readOnly: props.readOnly } : {}),
    ...(props.createOnly !== undefined ? { createOnly: props.createOnly } : {}),
    ...(props.editOnly !== undefined ? { editOnly: props.editOnly } : {}),
    ...(props.showWhen !== undefined ? { showWhen: props.showWhen } : {}),
    ...(props.prefill !== undefined ? { prefill: props.prefill } : {}),
    ...(props.slugFrom !== undefined ? { slugFrom: props.slugFrom } : {}),
    ...(props.title !== undefined ? { title: props.title } : {}),
    ...(props.body !== undefined ? { body: props.body } : {}),
    ...(props.kind !== undefined ? { kind: props.kind } : {}),
    ...(props.options !== undefined ? { options: props.options } : {}),
    ...(props.placeholder !== undefined ? { placeholder: props.placeholder } : {}),
    ...(props.description !== undefined ? { description: props.description } : {}),
  }));
}

function actionDescriptor(props: ActionProps): ActionDescriptor {
  return cachedDescriptor(actionDescriptorCache, props, () => ({
    id: props.id,
    label: props.label,
    ...(props.icon !== undefined ? { icon: props.icon } : {}),
    ...(props.disabled !== undefined ? { disabled: props.disabled } : {}),
    ...(props.danger !== undefined ? { danger: props.danger } : {}),
    ...(props.confirm !== undefined ? { confirm: props.confirm } : {}),
    ...(props.set !== undefined ? { set: props.set } : {}),
    ...(props.prompt !== undefined ? { prompt: props.prompt } : {}),
    ...(props.run !== undefined ? { run: props.run } : {}),
    ...(props.visibleWhen !== undefined ? { visibleWhen: props.visibleWhen } : {}),
  }));
}

function groupDescriptor(props: GroupProps): GroupDescriptor {
  return cachedDescriptor(groupDescriptorCache, props, () => ({
    ...(props.label !== undefined ? { label: props.label } : {}),
    ...(props.columns !== undefined ? { columns: props.columns } : {}),
    fields: parseDirectPageFields(props.children),
    actions: parsePageActions(props.children),
  }));
}

function tabDescriptor(props: TabProps): TabDescriptor {
  return cachedDescriptor(tabDescriptorCache, props, () => ({
    id: props.id,
    label: props.label,
    ...(props.icon !== undefined ? { icon: props.icon } : {}),
    ...(props.badge !== undefined ? { badge: props.badge } : {}),
    ...(props.hidden !== undefined ? { hidden: props.hidden } : {}),
    ...(props.children !== undefined ? { children: props.children } : {}),
  }));
}

function parseDirectPageFields(children: ReactNode): FieldDescriptor[] {
  return cachedChildDescriptors(directFieldListCache, children, () => {
    const fields = pageChildren(children).flatMap((child) => {
      const props = pageElementProps<FieldProps>(child, "field");
      return props ? [fieldDescriptor(props)] : [];
    });
    return assertUniqueDescriptor(fields, (field) => field.name, "field name");
  });
}

function assertUniqueDescriptor<TDescriptor>(
  descriptors: TDescriptor[],
  key: (descriptor: TDescriptor) => string,
  label: string,
): TDescriptor[] {
  const seen = new Set<string>();
  for (const descriptor of descriptors) {
    const value = key(descriptor);
    if (seen.has(value)) {
      throw new Error(`Duplicate page ${label}: ${value}`);
    }
    seen.add(value);
  }
  return descriptors;
}

function cachedDescriptor<TProps extends object, TDescriptor>(
  cache: WeakMap<object, unknown>,
  props: TProps,
  build: () => TDescriptor,
): TDescriptor {
  const cached = cache.get(props) as TDescriptor | undefined;
  if (cached) return cached;
  const descriptor = build();
  cache.set(props, descriptor);
  return descriptor;
}

function cachedChildDescriptors<TDescriptor>(
  cache: WeakMap<object, unknown>,
  children: ReactNode,
  parse: () => TDescriptor[],
): TDescriptor[] {
  const key = pageChildrenCacheKey(children);
  if (!key) return parse();
  const cached = cache.get(key) as TDescriptor[] | undefined;
  if (cached) return cached;
  const descriptors = parse();
  cache.set(key, descriptors);
  return descriptors;
}

export function parsePageFacets(children: ReactNode): FacetDescriptor[] {
  return cachedChildDescriptors(facetListCache, children, () => {
    const facets = pageChildren(children).flatMap((child) => {
      const props = pageElementProps<FacetProps>(child, "facet");
      return props ? [facetDescriptor(props)] : [];
    });
    return assertUniqueDescriptor(facets, (facet) => facet.field, "facet field");
  });
}

export function mergePageFacets(
  explicit: readonly FacetDescriptor[] | undefined,
  declared: readonly FacetDescriptor[],
): readonly FacetDescriptor[] {
  if (!explicit || explicit.length === 0) return declared;
  if (declared.length === 0) return explicit;
  return assertUniqueDescriptor(
    [...explicit, ...declared],
    (facet) => facet.field,
    "facet field",
  );
}

const columnDescriptorCache = new WeakMap<object, unknown>();
const facetDescriptorCache = new WeakMap<object, unknown>();
const fieldDescriptorCache = new WeakMap<object, unknown>();
const groupDescriptorCache = new WeakMap<object, unknown>();
const actionDescriptorCache = new WeakMap<object, unknown>();
const tabDescriptorCache = new WeakMap<object, unknown>();
const columnListCache = new WeakMap<object, unknown>();
const facetListCache = new WeakMap<object, unknown>();
const fieldListCache = new WeakMap<object, unknown>();
const groupListCache = new WeakMap<object, unknown>();
const actionListCache = new WeakMap<object, unknown>();
const tabListCache = new WeakMap<object, unknown>();
const directFieldListCache = new WeakMap<object, unknown>();
