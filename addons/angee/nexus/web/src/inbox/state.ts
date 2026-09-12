import { useCallback, useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Filter,
  routeSearchParam,
  updateRouteSearch,
  useRouteSearch,
  type ResourceViewFilter,
  type ResourceViewState,
  mergeResourceViewSearch,
  resourceViewStateToSearch,
} from "@angee/ui";
import {
  InboxFilter,
  NAVIGATOR_LENSES,
  RESULT_LENSES,
  resultLenses,
  type NavigatorLens,
  type ResultLens,
} from "./contract";

/** Live invalidation follows the records which own corpus membership and identity. */
export const INBOX_MODELS = [
  "messaging.Message",
  "messaging.Thread",
  "messaging.Part",
  "messaging.Participant",
  "messaging.MessageEdge",
  "messaging.MessageStar",
  "parties.Handle",
  "parties.Party",
  "parties.Circle",
  "parties.CircleMember",
  "parties.Relationship",
  "nexus.Tie",
  "storage.File",
  "integrate.Integration",
] as const;
export const PAGE_SIZE = 25;
export const COLLECTION_INITIAL = {
  pageSize: PAGE_SIZE,
  sorting: [{ id: "latest", desc: true }],
};
export const RESULTS_INITIAL = {
  ...COLLECTION_INITIAL,
  groupStack: [{ field: "by_conversation" }],
};
export const RELATED_TAB = "nexus-related";

/** Translate the native collection filter to the Messaging input contract. */
export function inboxQuery(
  filter: ResourceViewFilter,
  timezone = Intl.DateTimeFormat().resolvedOptions().timeZone,
) {
  const value = new InboxFilter(filter);
  return { coverage: value.coverage(timezone), search: value.search() };
}
export type InboxScope = ReturnType<typeof inboxQuery> & {
  sender: string;
  circle: string;
};

/** Main selection and the independent pinned backlink are owned by the router. */
export function useInboxNavigation() {
  const search = useRouteSearch();
  const navigate = useNavigate();
  const patch = useCallback(
    (values: Record<string, string | undefined>, replace = false) => {
      void navigate({ to: ".", search: updateRouteSearch(values), replace });
    },
    [navigate],
  );
  const param = (key: string) => routeSearchParam(search, key) ?? "";
  return useMemo(
    () => ({
      finder:
        NAVIGATOR_LENSES.find((lens) => lens === param("finder")) ?? "senders",
      lens:
        RESULT_LENSES.find((lens) => lens === param("lens")) ?? "conversations",
      sender: param("sender"),
      circle: param("circle"),
      message: param("message"),
      thread: param("thread"),
      at: param("at"),
      part: param("part"),
      tab: param("tab"),
      related: param("related"),
      relatedFrom: param("relatedFrom"),
      patch,
      changeNavigator: (finder: NavigatorLens, state: ResourceViewState) => {
        const filter =
          finder === "groups" || finder === "circles"
            ? Filter.from(state.filter).onlyFields(["text"])
            : state.filter;
        void navigate({
          to: ".",
          search: (current: Record<string, unknown>) =>
            mergeResourceViewSearch(
              { ...current, finder },
              resourceViewStateToSearch(
                {
                  ...state,
                  filter,
                  group: null,
                  groupStack: [],
                  pagination: { ...state.pagination, pageIndex: 0 },
                },
                COLLECTION_INITIAL,
              ),
              "s",
            ),
        });
      },
      changeResult: (lens: ResultLens, state: ResourceViewState) => {
        const axis = resultLenses[lens].group;
        const groupStack = axis ? [{ field: `by_${axis}` }] : [];
        void navigate({
          to: ".",
          search: (current: Record<string, unknown>) =>
            mergeResourceViewSearch(
              { ...current, lens },
              resourceViewStateToSearch(
                {
                  ...state,
                  group: groupStack[0] ?? null,
                  groupStack,
                  pagination: { ...state.pagination, pageIndex: 0 },
                },
                RESULTS_INITIAL,
              ),
              "c",
            ),
        });
      },
      read: (message: string, part?: string) =>
        patch({ message, part, tab: undefined }),
      conversation: (thread: string, message?: string) =>
        patch({
          thread,
          at: message ? `message:${message}` : undefined,
          message: undefined,
          part: undefined,
          tab: undefined,
        }),
      back: () =>
        patch({ message: undefined, part: undefined, tab: undefined }),
      results: () =>
        patch({
          thread: undefined,
          at: undefined,
          message: undefined,
          part: undefined,
          tab: undefined,
        }),
      select: (sender?: string, circle?: string) =>
        patch({
          sender,
          circle,
          "c.page": undefined,
          thread: undefined,
          at: undefined,
          message: undefined,
          part: undefined,
          tab: undefined,
        }),
      connect: (related: string, relatedFrom: string) =>
        patch({
          related,
          relatedFrom,
          "r.page": undefined,
          "r.filter": undefined,
          "r.sort": undefined,
          "r.group": undefined,
          "r.then": undefined,
          "r.pageSize": undefined,
          "r.view": undefined,
          "r.mode": undefined,
          "r.anchor": undefined,
        }),
      closeRelated: () =>
        patch({
          related: undefined,
          relatedFrom: undefined,
          "r.page": undefined,
          "r.filter": undefined,
          "r.sort": undefined,
          "r.group": undefined,
          "r.then": undefined,
          "r.pageSize": undefined,
          "r.view": undefined,
          "r.mode": undefined,
          "r.anchor": undefined,
        }),
    }),
    [search, patch, navigate],
  );
}
export type InboxNavigation = ReturnType<typeof useInboxNavigation>;
