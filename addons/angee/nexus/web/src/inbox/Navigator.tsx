import { useMemo } from "react";
import {
  CollectionTreeView, ListView, PaneLabelAction, PaneRow, PaneToolbarHost,
  RelativeTime, ScopeChip, Select, useResourceView,
  type ResourceViewFilter,
} from "@angee/ui";
import { InboxFilter, NAVIGATOR_LENSES, navigatorAxes } from "./contract";
import { InboxCoverageKind, useNavigatorControls } from "./Controls";
import { navigatorSource } from "./sources";
import type { InboxNavigatorRow } from "./documents";
import type { InboxNavigation } from "./state";
import { useNexusT } from "../i18n";

export function InboxNavigatorPane({ coverage, onCoverage, timezone, navigation }: {
  coverage: ResourceViewFilter;
  onCoverage: (next: ResourceViewFilter) => void;
  timezone: string;
  navigation: InboxNavigation;
}) {
  const t = useNexusT();
  const view = useResourceView();
  const lens = navigation.finder;
  const controls = useNavigatorControls(lens);
  const groupField = view.state.groupStack[0]?.field ?? "";
  const fading = new InboxFilter(view.state.filter).one("fading") === "yes";
  const source = useMemo(
    () => navigatorSource({ coverage, timezone, lens, groupField, fading, t }),
    [coverage, timezone, lens, groupField, fading, t],
  );
  const columns = useMemo(() => [
    { field: "label", header: t("inbox.name") }, { field: "latest", header: t("inbox.recent") },
    { field: "count", header: t("inbox.count") }, { field: "first", header: t("inbox.first") },
  ], [t]);
  const select = (row: InboxNavigatorRow) => {
    if (row.thread) navigation.conversation(row.thread.id);
    else if (row.circle) navigation.select(undefined, row.circle.id);
    else navigation.select(row.id);
  };
  const renderRow = (row: InboxNavigatorRow, interactive = true) => (
    <PaneRow icon={row.circle ? "users" : row.thread ? "channel" : "user"}
      title={row.label || t("inbox.conversation")} detail={row.count}
      meta={row.latest ? <RelativeTime value={row.latest} /> : undefined}
      sub={[row.handle?.value, row.preview].filter(Boolean).join(" · ")}
      selected={navigation.sender === row.id || Boolean(row.circle && navigation.circle === row.circle.id)}
      verb={row.thread ? t("inbox.openConversation") : t("inbox.results")}
      onOpen={interactive ? () => select(row) : undefined} />
  );
  const pane = {
      selector: <>
        <Select size="sm" className="min-w-0 w-24" aria-label={t("inbox.navigatorLens")} value={lens}
          options={NAVIGATOR_LENSES.map((value) => ({ value, label: t(`inbox.${value}`) }))}
          onValueChange={(finder) => {
            const selected = NAVIGATOR_LENSES.find((lens) => lens === finder);
            if (selected) navigation.changeNavigator(selected, view.state);
          }} />
        <InboxCoverageKind coverage={coverage} onCoverage={onCoverage} />
      </>,
      scope: <ScopeChip label={t("inbox.everyone")} active={!navigation.sender && !navigation.circle} onClick={() => navigation.select()} />,
  };
  return (
    <PaneToolbarHost role="region" aria-label={t("inbox.navigator")}>
      {lens === "circles" ? (
        <CollectionTreeView presentation="workspace" resource="nexus.InboxNavigator" source={source}
          columns={columns} rowKey="id" parent="parent_id" label="label" hasChildren="has_children"
          selectedId={navigation.circle ? `circle:${navigation.circle}` : undefined}
          onRowClick={select} renderRow={(row) => renderRow(row, false)}
          textFilterField="text" {...controls} pane={pane} emptyContent={t("inbox.noCircles")} />
      ) : (
        <ListView presentation="workspace" selectable={false} resource="nexus.InboxNavigator" source={source}
          columns={columns} availableViews={["list"]} textFilterField="text" maxGroupDepth={1}
          defaultExpandedGroups="none" {...controls}
          groupOptions={navigatorAxes(lens).map((axis) => ({ id: axis, label: t(`inbox.axis.${axis}`), group: { field: `by_${axis}` } }))}
          pane={pane} renderPaneRow={renderRow} emptyContent={t("inbox.noSenders")}
          renderGroupLabel={groupField === "by_circle" || groupField === "by_group" ? ({ bucket }) => {
            const id = bucket.key?.[groupField];
            return typeof id === "string" ? <PaneLabelAction icon="chevron-right"
              onClick={() => groupField === "by_circle" ? navigation.select(undefined, id) : navigation.conversation(id)}>
              {t(groupField === "by_circle" ? "inbox.results" : "inbox.openConversation")}
            </PaneLabelAction> : null;
          } : undefined} />
      )}
    </PaneToolbarHost>
  );
}
