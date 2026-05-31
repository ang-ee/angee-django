import {
  Children,
  Fragment,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";

export type PageElementKind = "column" | "field" | "group" | "action";

export const PAGE_ELEMENT_SLOT = Symbol.for("@angee/base.page.element");

export type PageElementType = {
  readonly [PAGE_ELEMENT_SLOT]?: PageElementKind;
};

export type PageElement<Props> = ReactElement<Props> & {
  type: PageElementType;
};

export function pageChildren(children: ReactNode): ReactNode[] {
  const nodes: ReactNode[] = [];
  for (const child of Children.toArray(children)) {
    if (isFragmentElement(child)) {
      nodes.push(...pageChildren(fragmentChildren(child)));
    } else {
      nodes.push(child);
    }
  }
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

function pageElementKind(type: unknown): PageElementKind | null {
  if (!type || (typeof type !== "function" && typeof type !== "object")) {
    return null;
  }
  const marker = (type as PageElementType)[PAGE_ELEMENT_SLOT];
  return marker ?? null;
}

function isFragmentElement(child: ReactNode): child is ReactElement {
  return isValidElement(child) && child.type === Fragment;
}

function fragmentChildren(child: ReactElement): ReactNode {
  return (child.props as { children?: ReactNode }).children;
}
