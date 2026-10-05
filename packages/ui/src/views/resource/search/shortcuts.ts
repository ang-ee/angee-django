import { useMemo, type ReactNode } from "react";
import type { ModelMetadata, ResourceQuery } from "@angee/metadata";
import { modelChain, useContainer, type ComposedContainerChild } from "../../../runtime/containers";
import type { SearchCatalog } from "./types";
import { rejectSearchDeclaration } from "./declaration-errors";

export type SearchShortcut =
  | { kind: "text"; field: string; label?: ReactNode; sequence?: number }
  | { kind: "facet"; field: string; label?: ReactNode; multiple?: boolean; sequence?: number }
  | { kind: "clause"; field: string; label?: ReactNode; sequence?: number }
  | { kind: "toggle"; id: string; label?: ReactNode; sequence?: number }
  | { kind: "group"; sequence?: number };

declare module "../../../runtime/containers" {
  interface ContainerKinds {
    /** Separate controls over a resource collection's search model. */
    search: SearchShortcut;
  }
}

export interface ListSearchDeclaration {
  /** Full box, or its badge trigger. Defaults to collapsed with declared or contributed shortcuts. */
  box?: true | "collapsed";
  /** Views over catalog choices, positioned with contributions by sequence. */
  shortcuts?: readonly SearchShortcut[];
}

/** Shared composition/render boundary; metadata remains the field capability owner. */
export function validateSearchShortcut(value: unknown, id: string, query?: ResourceQuery): asserts value is SearchShortcut {
  const fail = (reason: string): never => { throw new Error(`Search shortcut "${id}": ${reason}.`); };
  if (!value || typeof value !== "object") fail("expected a shortcut object");
  const shortcut = value as Record<string, unknown>;
  if (typeof shortcut.kind !== "string" || !["text", "facet", "clause", "toggle", "group"].includes(shortcut.kind)) fail(`unknown kind "${String(shortcut.kind)}"`);
  if (shortcut.sequence !== undefined && (typeof shortcut.sequence !== "number" || !Number.isFinite(shortcut.sequence))) fail("sequence must be a finite number");
  if (shortcut.kind === "facet" && shortcut.multiple !== undefined && typeof shortcut.multiple !== "boolean") fail("multiple must be a boolean");
  if (shortcut.kind === "toggle") {
    if (typeof shortcut.id !== "string" || !shortcut.id.trim()) fail("toggle requires a string id");
  } else if (shortcut.kind !== "group") {
    if (typeof shortcut.field !== "string" || !shortcut.field.trim()) fail("requires a string field");
  }
  if (query) {
    const reason = searchShortcutQueryError(value as SearchShortcut, query);
    if (reason) fail(reason);
  }
}

function searchShortcutQueryError(shortcut: SearchShortcut, query: ResourceQuery): string | undefined {
  if (shortcut.kind === "toggle" || shortcut.kind === "group") return;
  const operators = query.fields[shortcut.field]?.filter?.operators;
  if (!operators?.length) return `field "${shortcut.field}" is not filterable`;
  if (shortcut.kind === "text" && !operators.includes("iContains")) return `field "${shortcut.field}" does not support iContains`;
}

/** Page identities are reserved render-time extras, never addon-owned children. */
export function pageSearchShortcuts(declaration?: ListSearchDeclaration): readonly ComposedContainerChild<SearchShortcut>[] {
  if (declaration?.box !== undefined && declaration.box !== true && declaration.box !== "collapsed") {
    throw new Error('Search box must be true or "collapsed".');
  }
  const seen = new Set<string>();
  return (declaration?.shortcuts ?? []).map((content) => {
    validateSearchShortcut(content, "page");
    const id = `page.${content.kind}${content.kind === "group" ? "" : `.${content.kind === "toggle" ? content.id : content.field}`}`;
    if (seen.has(id)) throw new Error(`Duplicate search shortcut "${id}".`);
    seen.add(id);
    return { id, owner: "page", address: "resource#search", content, sequence: content.sequence };
  });
}

/** Resolve toolbar declarations and catalog text requests through the same container narrowing. */
export function useSearchShortcuts(declaration?: ListSearchDeclaration, metadata?: ModelMetadata | null) {
  const extra = useMemo(() => pageSearchShortcuts(declaration), [declaration]);
  const models = useMemo(() => modelChain(metadata?.resource.canonicalLabel, metadata?.resource.modelLabel), [metadata]);
  return useContainer<SearchShortcut>("resource#search", { models, extra });
}

/** Validate shapes everywhere; omit unavailable metadata-dependent targets in production. */
export function validateSearchShortcutCatalog(
  children: readonly ComposedContainerChild<SearchShortcut>[],
  catalog: SearchCatalog,
  { renderItem = false, query, reported }: { renderItem?: boolean; query?: ResourceQuery; reported?: Set<string> } = {},
): readonly ComposedContainerChild<SearchShortcut>[] {
  return children.filter(({ id, content }) => {
    validateSearchShortcut(content, id);
    if (content.kind === "group" && renderItem) throw new Error(`Search shortcut "${id}": grouping is unavailable with renderItem.`);
    const reason = query && searchShortcutQueryError(content, query);
    if (reason) return rejectSearchDeclaration("shortcut", id, reason, reported);
    const fail = (reason: string) => rejectSearchDeclaration("shortcut", id, reason, reported);
    switch (content.kind) {
      case "text": return catalog.text.some((item) => item.field === content.field) || fail(`text field "${content.field}" is unavailable`);
      case "facet": return catalog.facets.some((item) => item.field === content.field) || fail(`field "${content.field}" needs a facet catalog`);
      case "clause": return catalog.fields.some((item) => (item.field ?? item.id) === content.field) || fail(`field "${content.field}" is not filterable`);
      case "toggle": return catalog.filters.some((item) => item.id === content.id) || catalog.favorites.some((item) => item.id === content.id) || fail(`unknown toggle id "${content.id}"`);
      case "group": return true;
    }
  });
}
