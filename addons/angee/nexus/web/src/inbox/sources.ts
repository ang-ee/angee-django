import {
  collectionQuery,
  type CollectionGroupRequest,
  type CollectionPageRequest,
  type CollectionSource,
  type ResourceViewFilter,
} from "@angee/ui";
import {
  InboxConnections,
  InboxNavigator,
  InboxNavigatorGroups,
  InboxResultGroups,
  InboxResults,
  type InboxConnectionRow,
  type InboxNavigatorRow,
  type InboxResultRow,
} from "./documents";
import {
  InboxFilter,
  inboxCollectionQuery,
  navigatorAxes,
  navigatorOrder,
  resultLenses,
  type NavigatorLens,
  type ResultLens,
} from "./contract";
import { INBOX_MODELS, type InboxNavigation } from "./state";
import type { useNexusT } from "../i18n";

type Translate = ReturnType<typeof useNexusT>;

export function navigatorSource({
  coverage,
  timezone,
  lens,
  groupField,
  t,
}: {
  coverage: ResourceViewFilter;
  timezone: string;
  lens: NavigatorLens;
  groupField: string;
  t: Translate;
}): CollectionSource<InboxNavigatorRow> {
  const options = (request: CollectionPageRequest) => {
    const filter = new InboxFilter(request.filter);
    return {
      coverage: new InboxFilter(coverage).coverage(timezone),
      options: {
        lens,
        text: filter.one("text"),
        link: filter.one("link"),
        include_sent: filter.one("sent") === "yes",
        fading: filter.one("fading") === "yes",
        sort: navigatorOrder(request),
      },
      page: request.page,
      size: request.pageSize,
    };
  };
  return {
    query: inboxCollectionQuery(navigatorAxes(lens)),
    rows: collectionQuery({
      document: InboxNavigator,
      models: INBOX_MODELS,
      variables: (request: CollectionPageRequest) => ({
        ...options(request),
        scope: new InboxFilter(request.filter).scope(),
        parent: request.parentId?.replace(/^circle:/, "") || null,
      }),
      select: (data) => ({
        rows: data.inbox_navigator.rows,
        total: data.inbox_navigator.count,
        summary: t("inbox.unitSummary", {
          count: data.inbox_navigator.count,
          unit: t(`inbox.${lens}`),
          messages: data.inbox_navigator.message_count,
        }),
      }),
    }),
    groups: collectionQuery({
      document: InboxNavigatorGroups,
      models: INBOX_MODELS,
      variables: (request: CollectionGroupRequest) => ({
        ...options(request),
        axis: request.group.field.slice(3),
      }),
      select: (data) => ({
        count: data.inbox_navigator_groups.rows.reduce(
          (sum, row) => sum + row.count,
          0,
        ),
        totalCount: data.inbox_navigator_groups.count,
        buckets: data.inbox_navigator_groups.rows.map((row) => ({
          key: { [groupField]: row.value, label: row.label },
          count: row.count,
        })),
        summary: t("inbox.unitSummary", {
          count: data.inbox_navigator_groups.record_count,
          unit: t(`inbox.${lens}`),
          messages: data.inbox_navigator_groups.message_count,
        }),
      }),
    }),
  };
}

export function resultSource({
  navigation,
  timezone,
  lens,
  groupField,
  t,
}: {
  navigation: InboxNavigation;
  timezone: string;
  lens: ResultLens;
  groupField: string;
  t: Translate;
}): CollectionSource<InboxResultRow> {
  const options = (request: CollectionPageRequest) => {
    const filter = new InboxFilter(request.filter);
    return {
      coverage: filter.coverage(timezone),
      search: filter.search(),
      sender: navigation.sender,
      circle: navigation.circle,
      options: {
        lens,
        roles: filter.values("role"),
        oldest: request.order?.latest === "ASC",
        timezone: filter.one("timezone") || timezone,
      },
      page: request.page,
      size: request.pageSize,
    };
  };
  return {
    query: inboxCollectionQuery(resultLenses[lens].axes),
    leafPageSize: lens === "conversations" ? 3 : 25,
    rows: collectionQuery({
      document: InboxResults,
      models: INBOX_MODELS,
      variables: (request: CollectionPageRequest) => ({
        ...options(request),
        scope: new InboxFilter(request.filter).scope(),
      }),
      select: (data) => ({
        rows: data.inbox_results.rows,
        total: data.inbox_results.count,
        summary: t("inbox.unitSummary", {
          count: data.inbox_results.count,
          unit: t(`inbox.unit.${lens}`),
          messages: data.inbox_results.message_count,
        }),
      }),
    }),
    groups: collectionQuery({
      document: InboxResultGroups,
      models: INBOX_MODELS,
      variables: (request: CollectionGroupRequest) => ({
        ...options(request),
        axis: request.group.field.slice(3),
      }),
      select: (data) => ({
        count: data.inbox_result_groups.rows.reduce(
          (sum, row) => sum + row.count,
          0,
        ),
        totalCount: data.inbox_result_groups.count,
        buckets: data.inbox_result_groups.rows.map((row) => ({
          key: { [groupField]: row.value, label: row.label },
          count: row.count,
        })),
        summary: t("inbox.unitSummary", {
          count:
            lens === "conversations"
              ? data.inbox_result_groups.count
              : data.inbox_result_groups.record_count,
          unit: t(`inbox.unit.${lens}`),
          messages: data.inbox_result_groups.message_count,
        }),
      }),
    }),
  };
}

export function relatedSource(
  target: string,
  unit: string,
  t: Translate,
): CollectionSource<InboxConnectionRow> {
  return {
    query: inboxCollectionQuery([]),
    rows: collectionQuery({
      document: InboxConnections,
      models: INBOX_MODELS,
      enabled: Boolean(target),
      variables: (request: CollectionPageRequest) => ({
        target,
        text: new InboxFilter(request.filter).one("text"),
        oldest: request.order?.latest === "ASC",
        page: request.page,
        size: request.pageSize,
      }),
      select: (data) => ({
        rows: data.inbox_connections.rows,
        total: data.inbox_connections.count,
        summary: t("inbox.connectionSummary", {
          count: data.inbox_connections.count,
          total: data.inbox_connections.message_count,
          unit,
        }),
      }),
    }),
  };
}
