import { useMemo } from "react";
import { senderAvatarUrl } from "@angee/parties";
import {
  CollectionTreeView, ListView, PaneRow, PaneRows, PaneToolbarHost,
  AbsoluteTime, Select, avatarInitials, useResourceView,
  type ResourceViewFilter,
} from "@angee/ui";
import { InboxFilter, NAVIGATOR_LENSES, navigatorAxes } from "./contract";
import { useCoverageKind, useNavigatorControls } from "./Controls";
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
  const kind = useCoverageKind(coverage, onCoverage);
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
      avatar={!row.circle && !row.thread ? { initials: avatarInitials(row.label),
        src: row.party?.display_avatar?.url || senderAvatarUrl(row.handle) } : undefined}
      title={row.label || t("inbox.conversation")} detail={row.count}
      meta={row.latest ? <AbsoluteTime display="date" value={row.latest} timeZone={timezone} /> : undefined}
      sub={[row.handle?.value, row.preview].filter(Boolean).join(" · ")}
      selected={navigation.sender === row.id || Boolean(row.circle && navigation.circle === row.circle.id)}
      label={row.thread ? t("inbox.openConversation") : t("inbox.showExchanges", { label: row.label })}
      onOpen={interactive ? () => select(row) : undefined} />
  );
  // The band chooses how to explore; the root row below it clears the selection.
  const pane = {
      selector: <Select size="sm" className="min-w-0 flex-1" aria-label={t("inbox.navigatorLens")} value={lens}
          options={NAVIGATOR_LENSES.map((value) => ({ value, label: t(`inbox.${value}`) }))}
          onValueChange={(finder) => {
            const selected = NAVIGATOR_LENSES.find((lens) => lens === finder);
            if (selected) navigation.changeNavigator(selected, view.state);
          }} />,
      // The conversation-kind coverage is an occasional narrowing: it lives in the pane's overflow.
      overflowGroups: [{ label: t("inbox.kind"), value: kind.value, onValueChange: kind.setValue, items: kind.options }],
  };
  return (
    <PaneToolbarHost role="region" aria-label={t("inbox.navigator")}>
      <PaneRows className="shrink-0 px-2 py-1">
        <li><PaneRow icon="channel" title={t("inbox.anyKind")}
          sub={kind.value !== "any" ? kind.label : undefined}
          selected={!navigation.sender && !navigation.circle && !navigation.message && !navigation.thread}
          onOpen={() => navigation.select()} /></li>
      </PaneRows>
      {lens === "circles" ? (
        <CollectionTreeView presentation="workspace" resource="nexus.InboxNavigator" source={source}
          columns={columns} rowKey="id" parent="parent_id" label="label" hasChildren="has_children"
          selectedId={navigation.circle ? `circle:${navigation.circle}` : undefined}
          onRowClick={select} renderRow={(row) => renderRow(row, false)}
          textFilterField="text" {...controls} search={{ box: true }} pane={pane} emptyContent={t("inbox.noCircles")} />
      ) : (
        <ListView presentation="workspace" selectable={false} resource="nexus.InboxNavigator" source={source}
          columns={columns} availableViews={["list"]} textFilterField="text" maxGroupDepth={1}
          defaultExpandedGroups="none" {...controls} search={{ box: true }}
          groupOptions={navigatorAxes(lens).map((axis) => ({ id: axis, label: t(`inbox.axis.${axis}`), group: { field: `by_${axis}` } }))}
          pane={pane} renderPaneRow={renderRow} emptyContent={t("inbox.noNavigatorResults")}
          renderGroupLabel={groupField === "by_circle" || groupField === "by_group" ? ({ bucket, label }) => {
            const id = bucket.key?.[groupField];
            return typeof id === "string" ? <PaneRow icon={groupField === "by_circle" ? "users" : "channel"} title={label}
              onOpen={() => groupField === "by_circle" ? navigation.select(undefined, id) : navigation.conversation(id)} /> : undefined;
          } : undefined} />
      )}
    </PaneToolbarHost>
  );
}
