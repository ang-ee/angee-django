import { createContext, useContext, useMemo } from "react";
import { unarchivedFilter, type DataResourceMetadata, type QueryFilter } from "@angee/metadata";

/** Internal list decision shared by collection reads, including its dashboard.
 * ListView derives it from its presentation and enclosing saved record.
 */
export const ResourceViewArchiveScopeContext = createContext(true);

export function useArchiveScopeFilter(resource: DataResourceMetadata | null | undefined, filter: QueryFilter): QueryFilter;
export function useArchiveScopeFilter(resource: DataResourceMetadata | null | undefined, filter: QueryFilter | undefined): QueryFilter | undefined;
/** Apply the default archive scope unless the enclosing saved record exempts it. */
export function useArchiveScopeFilter(
  resource: DataResourceMetadata | null | undefined,
  filter: QueryFilter | undefined,
): QueryFilter | undefined {
  const scoped = useContext(ResourceViewArchiveScopeContext);
  return useMemo(() => scoped ? unarchivedFilter(resource, filter ?? {}) : filter, [scoped, resource, filter]);
}
