import { useCallback, useMemo } from "react";
import { useNavigate, useRouter } from "@tanstack/react-router";
import { Filter, localTimeZone, mergeResourceViewSearch, resourceViewStateToSearch, routeSearchParam, updateRouteSearch, useRouteSearch, type ResourceViewFilter, type ResourceViewState } from "@angee/ui";
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
  "storage.File",
  "integrate.Integration",
] as const;
/** Only the finder's fading predicate reads Nexus's derived ties. */
export const FADING_NAVIGATOR_MODELS = [...INBOX_MODELS, "nexus.Tie"] as const;
/** Source accounts, platforms and relation kinds over eligible messages and threads. */
export const INBOX_SOURCE_MODELS = [
  "messaging.Message",
  "messaging.Thread",
  "messaging.MessageEdge",
  "integrate.Integration",
] as const;
const PAGE_SIZE = 25;
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
  timezone = localTimeZone(),
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
  const router = useRouter();
  const patch = useCallback(
    (values: Record<string, string | undefined>, replace = false) => {
      void navigate({ to: ".", search: updateRouteSearch(values), replace });
    },
    [navigate],
  );
  const connect = useCallback(
    (related?: string, relatedFrom?: string) =>
      void navigate({
        to: ".",
        search: (current: Record<string, unknown>) => mergeResourceViewSearch(
          updateRouteSearch({ related, relatedFrom })(current), {}, "r",
        ),
      }),
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
      related: param("related"),
      relatedFrom: param("relatedFrom"),
      href: (values: Record<string, string | undefined>) => router.buildLocation({ to: ".", search: updateRouteSearch(values) }).href,
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
                  groupStack,
                  pagination: { ...state.pagination, pageIndex: 0 },
                },
                RESULTS_INITIAL,
              ),
              "c",
            ),
        });
      },
      read: (message: string, part?: string) => patch({ message, part }),
      conversation: (thread: string, message?: string) =>
        patch({
          thread,
          at: message ? `message:${message}` : undefined,
          message: undefined,
          part: undefined,
        }),
      results: () =>
        patch({
          thread: undefined,
          at: undefined,
          message: undefined,
          part: undefined,
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
        }),
      connect,
      /** A file previews in Links as the pinned target, beside the message that used it. */
      previewFile: (file: string, message: string) =>
        connect(`file:${file}`, message),
      closeRelated: () => connect(),
    }),
    [search, patch, connect, navigate, router],
  );
}
export type InboxNavigation = ReturnType<typeof useInboxNavigation>;
