import { useMemo } from "react";
import {
  Avatar,
  Button,
  CollectionTreeView,
  Glyph,
  ListView,
  PageHeader,
  RelativeTime,
  Select,
  Tag,
  avatarInitials,
  useResourceView,
  type ResourceViewFilter,
} from "@angee/ui";
import { NAVIGATOR_LENSES, navigatorAxes } from "./contract";
import {
  InboxOrder,
  InboxClearFilters,
  useNavigatorControls,
} from "./Controls";
import { navigatorSource } from "./sources";
import type { InboxNavigatorRow } from "./documents";
import type { InboxNavigation } from "./state";
import { useNexusT } from "../i18n";

export function InboxNavigatorPane({
  coverage,
  timezone,
  navigation,
}: {
  coverage: ResourceViewFilter;
  timezone: string;
  navigation: InboxNavigation;
}) {
  const t = useNexusT();
  const view = useResourceView();
  const lens = navigation.finder;
  const controls = useNavigatorControls(lens);
  const groupField = view.state.groupStack[0]?.field ?? "";
  const source = useMemo(
    () => navigatorSource({ coverage, timezone, lens, groupField, t }),
    [coverage, timezone, lens, groupField, t],
  );
  const columns = useMemo(
    () => [
      {
        field: "label",
        header: t(`inbox.${lens}`),
        headerVisuallyHidden: true,
        sortable: false,
        render: (row: InboxNavigatorRow) => (
          <NavigatorRow row={row} selected={navigation.sender === row.id} />
        ),
      },
    ],
    [lens, navigation.sender, t],
  );
  const select = (row: InboxNavigatorRow) => {
    if (row.thread) navigation.conversation(row.thread.id);
    else if (row.circle) navigation.select(undefined, row.circle.id);
    else navigation.select(row.id);
  };
  const toolbarActions = (
    <>
      <InboxOrder navigator />
      <InboxClearFilters finder />
    </>
  );
  return (
    <section
      aria-label={t("inbox.navigator")}
      className="flex h-full min-h-0 flex-col"
    >
      <PageHeader
        density="compact"
        headingLevel={2}
        title={
          <Select
            size="sm"
            aria-label={t("inbox.navigatorLens")}
            value={lens}
            options={NAVIGATOR_LENSES.map((value) => ({
              value,
              label: t(`inbox.${value}`),
            }))}
            onValueChange={(finder) => {
              const selected = NAVIGATOR_LENSES.find((lens) => lens === finder);
              if (selected) navigation.changeNavigator(selected, view.state);
            }}
          />
        }
        actions={
          <Button size="sm" variant="ghost" onClick={() => navigation.select()}>
            {t("inbox.everyone")}
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-auto">
        {lens === "circles" ? (
          <CollectionTreeView
            resource="nexus.InboxNavigator"
            source={source}
            columns={columns}
            rowKey="id"
            parent="parent_id"
            label="label"
            hasChildren="has_children"
            badge="count"
            selectedId={
              navigation.circle ? `circle:${navigation.circle}` : undefined
            }
            onRowClick={select}
            toolbarWrap
            textFilterField="text"
            {...controls}
            toolbarActions={toolbarActions}
            emptyContent={t("inbox.noCircles")}
          />
        ) : (
          <ListView
            resource="nexus.InboxNavigator"
            source={source}
            columns={columns}
            availableViews={["list"]}
            toolbarWrap
            textFilterField="text"
            maxGroupDepth={1}
            defaultExpandedGroups="none"
            {...controls}
            groupOptions={navigatorAxes(lens).map((axis) => ({
              id: axis,
              label: t(`inbox.axis.${axis}`),
              group: { field: `by_${axis}` },
            }))}
            toolbarActions={toolbarActions}
            onRowClick={select}
            emptyContent={t("inbox.noSenders")}
            renderGroupLabel={
              groupField === "by_circle" || groupField === "by_group"
                ? ({ label, bucket }) => {
                    const id = bucket.key?.[groupField];
                    return typeof id === "string" ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="min-w-0 justify-start px-0"
                        onClick={() =>
                          groupField === "by_circle"
                            ? navigation.select(undefined, id)
                            : navigation.conversation(id)
                        }
                      >
                        {label}
                        <Glyph name="chevron-right" />
                      </Button>
                    ) : (
                      label
                    );
                  }
                : undefined
            }
          />
        )}
      </div>
    </section>
  );
}

function NavigatorRow({
  row,
  selected,
}: {
  row: InboxNavigatorRow;
  selected: boolean;
}) {
  return (
    <div
      className={`flex min-w-0 items-start gap-2 py-2 ${selected ? "text-brand" : ""}`}
    >
      <Avatar size="sm" initials={avatarInitials(row.label)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{row.label}</span>
          <Tag className="ml-auto">{row.count}</Tag>
        </div>
        {row.handle ? (
          <p className="truncate text-2xs text-fg-muted">{row.handle.value}</p>
        ) : null}
        <p className="line-clamp-2 whitespace-normal text-2xs text-fg-muted">
          {row.preview}
        </p>
        {row.latest ? (
          <span className="text-2xs text-fg-muted">
            <RelativeTime value={row.latest} />
          </span>
        ) : null}
      </div>
    </div>
  );
}
