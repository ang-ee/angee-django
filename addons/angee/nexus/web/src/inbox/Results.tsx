import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { senderDisplayName } from "@angee/parties";
import { MessagePaneRow, useMessageChannelLabel } from "@angee/messaging";
import {
  AbsoluteTime, ListView, PaneLabelAction, PaneRow, PaneToolbarHost, ScopeChip, Select, optionLabel, useEnumOptions, useResourceView,
} from "@angee/ui";
import { RESULT_LENSES, resultLensForGroup } from "./contract";
import { useResultControls } from "./Controls";
import { resultSource } from "./sources";
import { InboxSelection, type InboxResultRow } from "./documents";
import { INBOX_MODELS, type InboxNavigation } from "./state";
import { useNexusT } from "../i18n";

export function InboxResultsPane({ navigation, timezone }: { navigation: InboxNavigation; timezone: string }) {
  const t = useNexusT();
  const view = useResourceView();
  const selection = useAuthoredQuery(InboxSelection, { sender: navigation.sender, circle: navigation.circle }, {
    models: INBOX_MODELS, enabled: Boolean(navigation.sender || navigation.circle),
  });
  const group = view.state.groupStack[0];
  const lens = resultLensForGroup(navigation.lens, group);
  const groupField = group?.field ?? "";
  const controls = useResultControls(lens);
  const source = useMemo(() => resultSource({ navigation, timezone, lens, groupField, t }), [navigation, timezone, lens, groupField, t]);
  const columns = useMemo(() => [{ field: "latest", header: t("inbox.latest") }], [t]);
  return (
    <PaneToolbarHost role="region" aria-label={t("inbox.results")}>
      <ListView presentation="workspace" selectable={false} resource="nexus.InboxResults" source={source}
        columns={columns} availableViews={["list"]} textFilterField="text" maxGroupDepth={1}
        defaultExpandedGroups="none" {...controls}
        pane={{ selector: <Select size="sm" aria-label={t("inbox.resultLens")} className="w-36" value={lens}
            options={RESULT_LENSES.map((value) => ({ value, label: t(`inbox.${value}`) }))}
            onValueChange={(value) => {
              const selected = RESULT_LENSES.find((lens) => lens === value);
              if (selected) navigation.changeResult(selected, view.state);
            }} />,
          scope: navigation.sender || navigation.circle ? <ScopeChip active
            label={selection.data?.inbox_selection?.label ?? (selection.error ? t("inbox.selectionUnavailable") : t("inbox.loading"))}
            onClick={() => navigation.select()} /> : undefined }}
        renderPaneRow={(row) => <ResultRow row={row} navigation={navigation} />}
        emptyContent={{ title: t("inbox.noResults"), description: t("inbox.noResultsHint") }}
        renderGroupLabel={groupField === "by_conversation" ? ({ bucket }) => {
          const target = bucket.key?.by_conversation;
          return typeof target === "string" ? <PaneLabelAction icon="chevron-right"
            onClick={() => target.startsWith("thread:") ? navigation.conversation(target.slice(7)) : navigation.read(target.slice(8))}>
            {t("inbox.openConversation")}
          </PaneLabelAction> : null;
        } : undefined} />
    </PaneToolbarHost>
  );
}

function ResultRow({ row, navigation }: { row: InboxResultRow; navigation: InboxNavigation }) {
  const t = useNexusT();
  const channelLabel = useMessageChannelLabel();
  const roles = useEnumOptions("messaging.Part", "role", { casing: "upper" });
  const message = row.message;
  const sender = senderDisplayName(message.sender);
  if (!row.part) return <MessagePaneRow message={message}
    selected={navigation.message === message.id} onOpen={() => navigation.read(message.id)} />;
  const available = Boolean(row.file || row.fragment);
  return <PaneRow icon={row.fragment ? "file" : "files"}
      title={row.file ? row.part.name || row.file.filename : row.fragment ? t("inbox.sharedText") : row.part.name || t("inbox.file")}
      detail={available ? optionLabel(roles, row.part.role) : t("inbox.fileUnavailable")} tone={available ? "muted" : "warning"}
      meta={<AbsoluteTime value={row.latest} display="date" />} sub={row.fragment?.text || [sender, channelLabel(message)].filter(Boolean).join(" · ")}
      verb={available ? t("inbox.matchingUses", { count: row.count }) : t("inbox.read")}
      onOpen={() => available ? navigation.connect(row.id, message.id) : navigation.read(message.id, row.part?.id)} />;
}
