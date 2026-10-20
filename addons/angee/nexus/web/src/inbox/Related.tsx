import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { MessagePaneRow } from "@angee/messaging";
import {
  Alert, Button, Dialog, PreviewPane, EmptyState, Glyph, ListView, PaneLabel, PaneRow,
  PaneRows, PaneToolbar, useResourceRecordHrefLookup,
} from "@angee/ui";
import { InboxRelatedTarget, type InboxConnectionRow } from "./documents";
import { relatedSource } from "./sources";
import { INBOX_MODELS, type InboxNavigation } from "./state";
import { useNexusT } from "../i18n";

/** The pinned target and its collection stay independent of main scope and coverage. */
export function InboxRelatedPane({ navigation }: { navigation: InboxNavigation }) {
  const t = useNexusT();
  const query = useAuthoredQuery(InboxRelatedTarget, { target: navigation.related, source: navigation.relatedFrom }, {
    models: INBOX_MODELS, enabled: Boolean(navigation.related),
  });
  const target = query.data?.inbox_related_target;
  const file = target?.file?.url ? {
    url: target.file.url, name: target.label, mime: target.file.mime_type?.mime_type, size: target.file.size_bytes,
  } : undefined;
  const source = useMemo(() => relatedSource(navigation.related, target?.unit ?? "messages", t), [navigation.related, target?.unit, t]);
  const columns = useMemo(() => [{ field: "latest", header: t("inbox.latest") }], [t]);
  const close = { icon: "x", label: t("inbox.closeRelated"), onClick: navigation.closeRelated };
  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t("inbox.related")}>
      {!navigation.related ? <>
        <PaneToolbar actions={[close]} />
        <EmptyState icon="link" title={t("inbox.related")} description={t("inbox.relatedHint")} />
      </> : <>
        {query.error ? <Alert tone="danger">{query.error.message}</Alert> : null}
        <PaneRows className="px-2 py-1">
          <PaneLabel count={target?.count}>
            {target && file ? <Dialog.Root key={target.id}>
              <Dialog.Trigger render={<Button size="sm" variant="ghost" className="max-w-full justify-start px-0" />}>
                <span className="truncate">{target.label}</span>
              </Dialog.Trigger>
              <Dialog.Portal><Dialog.Backdrop /><Dialog.Content size="lg" placement="center">
                <Dialog.Header className="flex items-center justify-between"><Dialog.Title>{target.label}</Dialog.Title><Dialog.Close /></Dialog.Header>
                <Dialog.Body><PreviewPane file={file} /></Dialog.Body>
              </Dialog.Content></Dialog.Portal>
            </Dialog.Root> : target?.label ?? t("inbox.loading")}
          </PaneLabel>
          {file ? <li><div role="region" aria-label={t("inbox.filePreview")} className="h-96 overflow-hidden rounded-6 border border-border-subtle">
            <PreviewPane file={file} />
          </div></li> : null}
          {target?.fragment ? <li className="line-clamp-4 whitespace-pre-wrap text-13 text-fg-muted">{target.fragment.text}</li> : null}
          {target?.source ? <li><MessagePaneRow message={target.source} detail={t("inbox.source")}
            onOpen={() => navigation.read(target.source!.id, target.part?.id)} /></li> : null}
        </PaneRows>
        <ListView presentation="workspace" selectable={false} resource="nexus.InboxConnections" source={source}
          columns={columns} availableViews={["list"]} textFilterField="text" groupOptions={[]}
          filterOptions={[]} customFilterFields={[]}
          pane={{ selector: <Button type="button" variant="ghost" size="iconSm" aria-label={close.label} title={close.label} onClick={close.onClick}><Glyph name={close.icon} /></Button> }}
          renderPaneRow={(row) => <ConnectionRow row={row} navigation={navigation} />}
          emptyContent={t("inbox.noRelated")} />
      </>}
    </section>
  );
}

function ConnectionRow({ row, navigation }: { row: InboxConnectionRow; navigation: InboxNavigation }) {
  const t = useNexusT();
  const href = useResourceRecordHrefLookup();
  const message = row.message;
  if (message) return <MessagePaneRow message={message}
    {...(message.id === navigation.relatedFrom ? { detail: t("inbox.source") } : {})}
    onOpen={() => navigation.read(message.id, row.part?.id)} />;
  if (row.handle) return <PaneRow icon="user" title={row.label} detail={row.role} sub={row.handle.value}
    href={href("parties.Handle", row.handle.id)} verb={t("graph.openRecord")} />;
  if (row.src && row.dst) return <PaneRows>
    <PaneLabel>{t(`inbox.relation.${row.label}`)}</PaneLabel>
    {[row.src, row.dst].map((message, index) => <li key={`${index}:${message.id}`}>
      <MessagePaneRow message={message} icon={index === 0 ? "arrow-up-right" : "arrow-down"} sub={row.provenance}
        meta={row.confidence !== null ? `${Math.round(row.confidence * 100)}%` : undefined}
        onOpen={() => navigation.read(message.id)} />
    </li>)}
  </PaneRows>;
  return <PaneRow icon="link" title={row.label} detail={row.role} />;
}
