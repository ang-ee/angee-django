import * as React from "react";
import { type Row } from "@angee/metadata";
import { type ListColumn, type ListViewProps } from "../ListView";
import { type FormViewProps } from "../../form/FormView";
import type { ListProps } from "../List";
import type { FormProps } from "../../form/Form";
import { type ResourceViewDefaultGroups, type ResourceViewGroup, type ResourceViewKind } from "../resource-view-model";
import { parsePageActions, parsePageColumns, parsePageFacets, parsePageFields, parsePageGroups, mergePageFacets, pageChildren, pageElementProps, requirePageColumns } from "../../page";
import { formDeclarationCache, listDeclarationCache, unrecognizedResourceListChildMessage } from "./child-dsl";
import type { ResourceFormDeclaration, ResourceListDeclaration, ResourceListDeclarations, ResourceListProps } from "./public";
export function parseResourceListDeclarations<TRow extends Row = Row>(
  children: React.ReactNode,
): ResourceListDeclarations<TRow> {
  let list: ResourceListDeclaration<TRow> | undefined;
  let form: ResourceFormDeclaration | undefined;

  for (const child of pageChildren(children)) {
    if (!React.isValidElement(child)) {
      throw new Error(unrecognizedResourceListChildMessage(child));
    }

    const listProps = pageElementProps<ListProps<TRow>>(child, "list");
    if (listProps) {
      if (list) throw new Error("ResourceList accepts only one List child.");
      list = resourceListDeclaration(listProps);
      continue;
    }

    const formProps = pageElementProps<FormProps>(child, "form");
    if (formProps) {
      if (form) throw new Error("ResourceList accepts only one Form child.");
      form = resourceFormDeclaration(formProps);
      continue;
    }

    throw new Error(unrecognizedResourceListChildMessage(child));
  }

  return {
    ...(list ? { list } : {}),
    ...(form ? { form } : {}),
  };
}

function resourceListDeclaration<TRow extends Row>(
  props: ListProps<TRow>,
): ResourceListDeclaration<TRow> {
  const cached = listDeclarationCache.get(props) as
    | ResourceListDeclaration<TRow>
    | undefined;
  if (cached) return cached;
  const declaration = {
    props,
    columns: requirePageColumns("List", parsePageColumns<TRow>(props.children)),
    facets: mergePageFacets(props.facets, parsePageFacets(props.children)),
  };
  listDeclarationCache.set(props, declaration);
  return declaration;
}

function resourceFormDeclaration(props: FormProps): ResourceFormDeclaration {
  const cached = formDeclarationCache.get(props);
  if (cached) return cached;
  const declaration = {
    props,
    fields: parsePageFields(props.children),
    groups: parsePageGroups(props.children),
    actions: parsePageActions(props.children),
  };
  formDeclarationCache.set(props, declaration);
  return declaration;
}

export function validateResourceListDeclarations<TRow extends Row>(
  props: Omit<ResourceListProps<TRow>, "children">,
  declarations: ResourceListDeclarations<TRow>,
): void {
  validateResourceListRouting(props);
  validateNestedResource("List", props.resource, declarations.list?.props.resource);
  validateNestedResource("Form", props.resource, declarations.form?.props.resource);
  if (declarations.list) {
    validateNestedDeclaration({
      owner: "List",
      resourceListProps: props,
      elementProps: declarations.list.props,
      declarationKeys: ["columns"],
      resourceListOwnedKeys: [
        "onCreate",
        "onCreateInLane",
        "onRowClick",
        "onListStateChange",
      ],
    });
    if (declarations.list.facets.length > 0 && hasOwnDefined(props, "facets")) {
      throw new Error(
        `ResourceList and its List child both declare "facets".`,
      );
    }
  }
  if (declarations.form) {
    validateNestedDeclaration({
      owner: "Form",
      resourceListProps: props,
      elementProps: declarations.form.props,
      declarationKeys: ["formFields", "formGroups"],
      resourceListOwnedKeys: ["id", "onSaved"],
    });
  }
}

function validateResourceListRouting<TRow extends Row>(
  props: Omit<ResourceListProps<TRow>, "children">,
): void {
  if (props.routed) {
    const controlledKeys = ["recordId", "creating", "onSelect", "onClose"];
    const mixed = controlledKeys.filter((key) => hasOwnDefined(props, key));
    if (mixed.length > 0) {
      throw new Error(
        `ResourceList routed mode cannot mix with controlled record props: ${mixed.join(", ")}.`,
      );
    }
    return;
  }
}

function validateNestedDeclaration<TRow extends Row>({
  owner,
  resourceListProps,
  elementProps,
  declarationKeys,
  resourceListOwnedKeys,
}: {
  owner: "List" | "Form";
  resourceListProps: Omit<ResourceListProps<TRow>, "children">;
  elementProps: object;
  declarationKeys: readonly string[];
  resourceListOwnedKeys: readonly string[];
}): void {
  const ownedKeys = new Set(resourceListOwnedKeys);
  for (const key of resourceListOwnedKeys) {
    if (hasOwnDefined(elementProps, key)) {
      throw new Error(`ResourceList owns ${owner} child "${key}" wiring.`);
    }
  }
  for (const key of declarationKeys) {
    if (hasOwnDefined(resourceListProps, key)) {
      throw new Error(
        `ResourceList and its ${owner} child both declare "${key}".`,
      );
    }
  }
  for (const key of Object.keys(elementProps)) {
    if (key === "children" || key === "resource" || ownedKeys.has(key)) continue;
    if (hasOwnDefined(resourceListProps, key)) {
      throw new Error(
        `ResourceList and its ${owner} child both declare "${key}".`,
      );
    }
  }
}

function validateNestedResource(
  owner: string,
  pageResource: string,
  nestedResource: string | undefined,
): void {
  if (!nestedResource || nestedResource === pageResource) return;
  throw new Error(
    `${owner} resource "${nestedResource}" does not match ResourceList resource "${pageResource}".`,
  );
}

export function requiredColumns<TRow extends Row>(
  columns: readonly ListColumn<TRow>[] | undefined,
): readonly ListColumn<TRow>[] {
  if (columns) return columns;
  throw new Error("ResourceList requires columns or a List child.");
}

export function listElementRenderProps<TRow extends Row>(
  props: ListProps<TRow>,
): Partial<ListViewProps<TRow> & {
  defaultView?: ResourceViewKind;
  defaultGroup?: ResourceViewGroup | null;
  defaultGroups?: ResourceViewDefaultGroups;
}> {
  const {
    children: _children,
    facets: _facets,
    list: _list,
    laneSource: _laneSource,
    resource: _model,
    onCreate: _onCreate,
    onCreateInLane: _onCreateInLane,
    onRowClick: _onRowClick,
    onListStateChange: _onListStateChange,
    ...forwarded
  } = props;
  return forwarded;
}

export function mergeCreateDefaults(
  base: Record<string, unknown> | undefined,
  quick: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!quick) return base;
  return { ...base, ...quick };
}

export function formElementRenderProps(props: FormProps): Partial<FormViewProps> {
  const {
    children: _children,
    id: _id,
    resource: _model,
    onSaved: _onSaved,
    ...forwarded
  } = props;
  return forwarded;
}

function hasOwnDefined(object: object, key: string): boolean {
  return (
    Object.prototype.hasOwnProperty.call(object, key) &&
    (object as Record<string, unknown>)[key] !== undefined
  );
}
