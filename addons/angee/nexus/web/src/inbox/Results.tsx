import { useMemo, useState } from "react";
import { senderDisplayName } from "@angee/parties";
import { MessagePaneRow, useMessageChannelLabel } from "@angee/messaging";
import {
  AbsoluteTime, Button, Dialog, Glyph, ListView, PaneRow, PreviewPane, RecordHeader, Select,
  optionLabel, useEnumOptions, useResourceView,
} from "@angee/ui";
import type { GroupLabelContext } from "@angee/ui/views/resource-view-types";
import { RESULT_LENSES, resultLensForGroup } from "./contract";
import { useResultControls } from "./Controls";
import { resultSource } from "./sources";
import type { InboxResultRow } from "./documents";
import type { InboxNavigation } from "./state";
import { useInboxBreadcrumbs } from "./breadcrumbs";
import { useNexusT } from "../i18n";

/** A lens changes the row unit; the standard resource toolbar still owns the query. */
export function InboxResultsPane({ navigation, timezone }: { navigation: InboxNavigation; timezone: string }) {
  const t = useNexusT();
  const view = useResourceView();
  const selected = Boolean(navigation.sender || navigation.circle);
  const selectionLabel = useInboxBreadcrumbs(navigation);
  const group = view.state.groupStack[0];
  const lens = resultLensForGroup(navigation.lens, group);
  const groupField = group?.field ?? "";
  const controls = useResultControls(lens);
  const source = useMemo(() => resultSource({ navigation, timezone, lens, groupField, t }), [navigation, timezone, lens, groupField, t]);
  const columns = useMemo(() => [{ field: "latest", header: t("inbox.latest") }], [t]);
  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col" aria-label={t("inbox.results")}>
      <RecordHeader density="compact" title={selected
        ? selectionLabel
        : t("inbox.anyKind")}
        meta={selected ? t("inbox.selectionHint") : t("inbox.coverageHint", { timezone })}
        actions={selected ? <Button size="iconSm" variant="ghost" aria-label={t("inbox.clearSelection")}
          title={t("inbox.clearSelection")} onClick={() => navigation.select()}><Glyph name="x" /></Button> : undefined} />
      <ListView presentation="workspace" selectable={false} resource="nexus.InboxResults" source={source}
        columns={columns} availableViews={["list"]} textFilterField="text" maxGroupDepth={1}
        defaultExpandedGroups="all" {...controls} search={{ box: true }}
        toolbarActions={<>
          <Select size="sm" aria-label={t("inbox.resultLens")} className="w-36" value={lens}
            options={RESULT_LENSES.map((value) => ({ value, label: t("inbox." + value) }))}
            onValueChange={(value) => {
              const next = RESULT_LENSES.find((item) => item === value);
              if (next) navigation.changeResult(next, view.state);
            }} />
          <Select size="sm" aria-label={t("inbox.order")} className="w-32"
            value={view.state.sorting?.[0]?.desc === false ? "oldest" : "latest"}
            options={[{ value: "latest", label: t("inbox.recent") }, { value: "oldest", label: t("inbox.oldest") }]}
            onValueChange={(value) => view.setSorting([{ id: "latest", desc: value !== "oldest" }])} />
        </>}
        renderPaneRow={(row) => <ResultRow row={row} navigation={navigation} />}
        emptyContent={{ title: t("inbox.noResults"), description: t("inbox.noResultsHint") }}
        renderGroupLabel={groupField === "by_conversation" ? (context) =>
          <ConversationLabel {...context} timezone={timezone} navigation={navigation} /> : undefined} />
    </section>
  );
}

function ConversationLabel({ bucket, label, timezone, navigation }: GroupLabelContext & { timezone: string; navigation: InboxNavigation }) {
  const t = useNexusT();
  const platforms = useEnumOptions("messaging.Message", "platform", { casing: "upper" });
  const target = bucket.key?.by_conversation;
  if (typeof target !== "string") return label;
  const platform = bucket.key?.platform;
  const account = bucket.key?.account;
  const latest = bucket.max?.latest;
  return <PaneRow icon="channel" title={label || t("inbox.conversation")}
    sub={[typeof platform === "string" && platform ? optionLabel(platforms, platform) : undefined,
      typeof account === "string" ? account : undefined].filter(Boolean).join(" · ")}
    meta={typeof latest === "string" ? <AbsoluteTime value={latest} timeZone={timezone} display="date" /> : undefined}
    label={t("inbox.openConversation")}
    onOpen={() => target.startsWith("thread:") ? navigation.conversation(target.slice(7)) : navigation.read(target.slice(8))} />;
}

function ResultRow({ row, navigation }: { row: InboxResultRow; navigation: InboxNavigation }) {
  const t = useNexusT();
  const [preview, setPreview] = useState(false);
  const channelLabel = useMessageChannelLabel();
  const roles = useEnumOptions("messaging.Part", "role", { casing: "upper" });
  const message = row.message;
  const sender = senderDisplayName(message.sender);
  if (!row.part) return <MessagePaneRow message={message}
    selected={navigation.message === message.id} onOpen={() => navigation.read(message.id)}
    onOpenConversation={message.thread ? () => navigation.conversation(message.thread!.id, message.id) : undefined} />;
  const available = Boolean(row.file || row.fragment);
  const title = row.file ? row.part.name || row.file.filename : row.fragment ? t("inbox.sharedText") : row.part.name || t("inbox.file");
  const file = row.file?.url ? {
    url: row.file.url, name: title, mime: row.file.mime_type?.mime_type, size: row.file.size_bytes,
  } : undefined;
  const readSource = () => navigation.read(message.id, row.part!.id);
  return <>
    <PaneRow icon={row.fragment ? "file" : "files"} title={title}
      detail={available ? optionLabel(roles, row.part.role) : t("inbox.fileUnavailable")} tone={available ? "muted" : "warning"}
      meta={<AbsoluteTime value={row.latest} display="date" />}
      sub={row.fragment?.text || [sender, channelLabel(message)].filter(Boolean).join(" · ")}
      label={file ? t("inbox.filePreview") : t("inbox.readSource")}
      verb={<div className="flex items-center gap-1">
        {available ? <Button size="sm" variant="secondary" onClick={() => navigation.connect(row.id, message.id)}>
          <Glyph name="link" />{t("inbox.matchingUses", { count: row.count })}
        </Button> : null}
        {row.file ? <Button size="iconSm" variant="ghost" aria-label={t("inbox.readSource")} title={t("inbox.readSource")}
          onClick={readSource}><Glyph name="channel" /></Button> : null}
      </div>}
      onOpen={() => file ? setPreview(true) : readSource()} />
    {file ? <Dialog.Root open={preview} onOpenChange={setPreview}>
      <Dialog.Portal><Dialog.Backdrop /><Dialog.Content size="lg" placement="center">
        <Dialog.Header className="flex items-center justify-between"><Dialog.Title>{title}</Dialog.Title><Dialog.Close /></Dialog.Header>
        <Dialog.Body><PreviewPane file={file} /></Dialog.Body>
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root> : null}
  </>;
}
