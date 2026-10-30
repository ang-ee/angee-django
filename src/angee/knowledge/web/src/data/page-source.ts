import { Filter, ResourceQuery, type ModelMetadata, type QueryFilter } from "@angee/metadata";
import { collectionQuery, type CollectionPageRequest, type CollectionSource } from "@angee/ui";
import { KnowledgePages, PAGE_MODEL, type KnowledgePageRow } from "./documents";
import { pageIcon } from "./page-rows";

/** The tree and its record pager share one server-side branch predicate. */
export function pageCollectionFilter(vault: string, request: CollectionPageRequest, scope?: QueryFilter): QueryFilter {
  const branch = request.parentId ? { parent: { exact: request.parentId } }
    : Filter.from(request.filter).hasEntries() || scope ? {} : { navigation_root: { exact: true } };
  return Filter.from({ vault: { exact: vault }, is_trashed: { exact: false } })
    .and(Filter.from(scope).and(Filter.from(request.filter).and(branch)));
}

/** Knowledge's resource projection; collection views own paging, search and loading. */
export function pageSource(metadata: ModelMetadata, vault: string, scope?: QueryFilter): CollectionSource<KnowledgePageRow> {
  const query = ResourceQuery.from(metadata);
  return {
    query,
    rows: collectionQuery({
      document: KnowledgePages, models: [PAGE_MODEL], enabled: Boolean(vault),
      variables: (request: CollectionPageRequest) => ({
        where: query.toWhere(pageCollectionFilter(vault, request, scope)),
        order: [query.toOrderBy(request.order ?? { title: "ASC" })],
        limit: request.pageSize, offset: (request.page - 1) * request.pageSize,
      }),
      select: (data) => ({
        rows: data.pages.map((page) => ({ ...page, icon: page.icon || pageIcon(page.kind) })),
        total: data.pages_aggregate.aggregate?.count ?? 0,
      }),
    }),
  };
}
