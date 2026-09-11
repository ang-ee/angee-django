import { useCallback, useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Filter, routeSearchParam, updateRouteSearch, useResourceView, useRouteSearch, type ResourceViewFilter } from "@angee/ui";

/** Live invalidation follows the records which own corpus membership and identity. */
export const INBOX_MODELS = ["messaging.Message", "messaging.Thread", "messaging.MessageStar", "parties.Handle", "parties.Party", "parties.Circle", "parties.CircleMember", "storage.File", "integrate.Integration"] as const;
export const PAGE_SIZE = 25;
export const COLLECTION_INITIAL = { pageSize: PAGE_SIZE };
export const RELATED_TAB = "nexus-related";

/** Translate the native collection filter to the Messaging input contract. */
export function inboxQuery(filter: ResourceViewFilter) {
  const value = Filter.from(filter);
  return {
    coverage: {
      platforms: [...value.facetValues("platform")], accounts: [...value.facetValues("account")], kinds: [...value.facetValues("kind")],
      after: value.facetValues("after")[0] || null, before: value.facetValues("before")[0] || null,
    },
    search: {
      text: value.textTerm("text"), quoted: value.facetValues("quoted").includes("yes"),
      attachment: value.facetValues("attachment")[0] ?? "", direction: value.facetValues("direction")[0] ?? "",
      starred: value.facetValues("starred").includes("yes"), handle: value.facetValues("handle")[0] ?? "",
    },
  };
}
export type InboxScope = ReturnType<typeof inboxQuery> & { sender: string; circle: string };

/** Main selection and the independent pinned backlink are owned by the router. */
export function useInboxNavigation() {
  const search = useRouteSearch();
  const navigate = useNavigate();
  const patch = useCallback((values: Record<string, string | undefined>, replace = false) => {
    void navigate({ to: ".", search: updateRouteSearch(values), replace });
  }, [navigate]);
  const param = (key: string) => routeSearchParam(search, key) ?? "";
  return useMemo(() => ({
    sender: param("sender"), circle: param("circle"), message: param("message"), thread: param("thread"),
    at: param("at"), part: param("part"), tab: param("tab"), related: param("related"), relatedFrom: param("relatedFrom"),
    patch,
    read: (message: string, part?: string) => patch({ message, part, tab: undefined }),
    conversation: (thread: string, message?: string) => patch({ thread, at: message ? `message:${message}` : undefined, message: undefined, part: undefined, tab: undefined }),
    back: () => patch({ message: undefined, part: undefined, tab: undefined }),
    results: () => patch({ thread: undefined, at: undefined, message: undefined, part: undefined, tab: undefined }),
    select: (sender?: string, circle?: string) => patch({ sender, circle, "c.page": undefined, thread: undefined, at: undefined, message: undefined, part: undefined, tab: undefined }),
    connect: (related: string, relatedFrom: string) => patch({ related, relatedFrom, "r.page": undefined, "r.filter": undefined }),
    closeRelated: () => patch({ related: undefined, relatedFrom: undefined }),
  }), [search, patch]);
}
export type InboxNavigation = ReturnType<typeof useInboxNavigation>;

export function useInboxFilter() {
  const view = useResourceView();
  const filter = Filter.from(view.state.filter);
  return {
    ...view, filter,
    setText: (text: string) => view.setFilter(filter.withTextTerm(text, "text")),
    setValue: (field: string, value: string) => view.setFilter({ ...filter.withoutFields([field]), ...(value ? { [field]: { exact: value } } : {}) }),
    toggle: (field: string, value: string) => view.setFilter(filter.toggleFacet({ field, value })),
  };
}
