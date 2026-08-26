import * as React from "react";
import type { ResourceFormDeclaration } from "./public";
export function composeNodes(
  first: React.ReactNode,
  second: React.ReactNode,
): React.ReactNode {
  if (first == null || first === false) return second ?? null;
  if (second == null || second === false) return first;
  return (
    <>
      {first}
      {second}
    </>
  );
}

export function unrecognizedResourceListChildMessage(child: React.ReactNode): string {
  return (
    `ResourceList child ${resourceChildName(child)} is not a List or Form ` +
    "declaration; wrapper components hide the marker from the parser."
  );
}

function resourceChildName(child: React.ReactNode): string {
  if (React.isValidElement(child)) return elementTypeName(child.type);
  if (typeof child === "string") return `text "${child.trim()}"`;
  return typeof child;
}

function elementTypeName(type: unknown): string {
  if (typeof type === "string") return `<${type}>`;
  if (typeof type === "function") {
    const component = type as { displayName?: string; name?: string };
    return component.displayName ?? component.name ?? "anonymous component";
  }
  if (typeof type === "object" && type !== null) {
    const record = type as { displayName?: string };
    return record.displayName ?? "component";
  }
  return "component";
}

export const listDeclarationCache = new WeakMap<object, unknown>();
export const formDeclarationCache = new WeakMap<object, ResourceFormDeclaration>();
