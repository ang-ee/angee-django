import {
  Fragment,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";

export type PageElementKind =
  | "column"
  | "facet"
  | "field"
  | "group"
  | "action"
  | "list"
  | "form"
  | "metric"
  | "tab";

export const PAGE_ELEMENT_SLOT = Symbol.for("@angee/base.page.element");

export type PageElementType = {
  readonly [PAGE_ELEMENT_SLOT]?: PageElementKind;
};

export type PageElement<Props> = ReactElement<Props> & {
  type: PageElementType;
};

export function pageChildren(children: ReactNode): ReactNode[] {
  const nodes: ReactNode[] = [];
  appendPageChildren(nodes, children);
  return nodes;
}

export function pageElementProps<Props>(
  child: ReactNode,
  kind: PageElementKind,
): Props | null {
  if (!isValidElement(child)) return null;
  const childKind = pageElementKind(child.type);
  if (childKind !== kind) return null;
  return child.props as Props;
}

export function pageElementKind(type: unknown): PageElementKind | null {
  if (!type || (typeof type !== "function" && typeof type !== "object")) {
    return null;
  }
  const marker = (type as PageElementType)[PAGE_ELEMENT_SLOT];
  return marker ?? null;
}

export function pageChildrenCacheKey(children: ReactNode): object | null {
  if (isValidElement(children)) return children.props as object;
  return Array.isArray(children) ? children : null;
}

function appendPageChildren(nodes: ReactNode[], child: ReactNode): void {
  if (child == null || typeof child === "boolean") return;
  if (Array.isArray(child)) {
    for (const item of child) appendPageChildren(nodes, item);
    return;
  }
  if (isFragmentElement(child)) {
    appendPageChildren(nodes, fragmentChildren(child));
    return;
  }
  nodes.push(child);
}

function isFragmentElement(child: ReactNode): child is ReactElement {
  return isValidElement(child) && child.type === Fragment;
}

function fragmentChildren(child: ReactElement): ReactNode {
  return (child.props as { children?: ReactNode }).children;
}
