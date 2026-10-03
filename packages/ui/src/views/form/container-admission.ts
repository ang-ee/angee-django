import * as React from "react";
import type { Row } from "@angee/metadata";

import { EMPTY_CONTAINERS, resolveContainer, useAppRuntime, type ComposedContainerChild } from "../../runtime";
import { optionToken } from "../../widgets/types";

/** A row's implementation tokens (its `ImplClassField` values), which select `impl` children and variants. */
export function rowImplementations(implFields: readonly string[] | undefined, record: Row | null | undefined): readonly string[] {
  return (implFields ?? []).flatMap((field) => {
    const impl = optionToken(record?.[field]);
    return impl ? [impl] : [];
  });
}

/** The children whose presence turns on the row: `impl` children, variants, and the originals they stand in for. */
export function rowDependentChildren(entries: readonly ComposedContainerChild[]): ReadonlySet<string> {
  const ids = new Set<string>();
  for (const entry of entries) {
    if (entry.impl !== undefined) ids.add(entry.id);
    if (entry.variant) ids.add(entry.id).add(entry.variant.of);
  }
  return ids;
}

/**
 * Whether a record admits one child of a container: the row's implementation
 * selects its `impl` children and variants, and a variant the row may not see
 * leaves its original (G-13). For owners that resolve a container once for
 * every candidate (`projection`), so the record projection reads all their
 * fields, and decide per record at render. Each record resolves once; without
 * a record no implementation is known, so originals stand.
 */
export function useContainerAdmission(
  address: string,
  models: readonly string[],
  implFields: readonly string[] | undefined,
): (id: string, record: Row | null | undefined) => boolean {
  const { containers = EMPTY_CONTAINERS, containerScope } = useAppRuntime();
  return React.useMemo(() => {
    const scope = containerScope ? { scope: containerScope } : {};
    const ids = (children: readonly ComposedContainerChild[]): ReadonlySet<string> => new Set(children.map((child) => child.id));
    const byRecord = new WeakMap<Row, ReadonlySet<string>>();
    let withoutRecord: ReadonlySet<string> | undefined;
    const admitted = (record: Row | null | undefined): ReadonlySet<string> => {
      if (!record) return withoutRecord ??= ids(resolveContainer(containers, address, { models, ...scope }));
      let found = byRecord.get(record);
      if (!found) {
        found = ids(resolveContainer(containers, address, { models, row: record, impls: rowImplementations(implFields, record), ...scope }));
        byRecord.set(record, found);
      }
      return found;
    };
    return (id, record) => admitted(record).has(id);
  }, [address, containerScope, containers, implFields, models]);
}
