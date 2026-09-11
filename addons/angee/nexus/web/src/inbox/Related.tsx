import { useAuthoredQuery } from "@angee/refine";
import { senderDisplayName } from "@angee/parties";
import { Alert, Button, EmptyState, Glyph, LoadingPanel, PageHeader, Pager, RelativeTime, DebouncedSearchInput, Select, Tag, TimelineView } from "@angee/ui";
import { InboxRelated } from "./documents";
import { useNexusT } from "../i18n";
import { INBOX_MODELS, PAGE_SIZE, useInboxFilter, type InboxNavigation } from "./state";

/** Related has its own collection state and deliberately receives no main scope. */
export function InboxRelatedPane({ navigation }: { navigation: InboxNavigation }) {
  const t = useNexusT();
  const view = useInboxFilter();
  const query = useAuthoredQuery(InboxRelated, { target: navigation.related, text: view.filter.textTerm("text"), page: view.state.pagination.pageIndex + 1, oldest: view.state.sorting?.[0]?.desc === false }, { models: INBOX_MODELS, enabled: Boolean(navigation.related) && !view.state.queryError });
  const page = query.data?.inbox_related;
  const [kind, id] = navigation.related.split(":");
  const part = page?.rows.flatMap(row => row.parts).find(part => kind === "file" ? part.file?.id === id : part.fragment?.id === id);
  const error = view.state.queryError ?? query.error;
  return <section className="flex h-full min-h-0 flex-col" aria-label={t("inbox.related")}>
    <PageHeader density="compact" headingLevel={2} title={t("inbox.related")} actions={<Button size="iconSm" variant="ghost" aria-label={t("inbox.closeRelated")} onClick={navigation.closeRelated}><Glyph name="x" /></Button>} />
    {!navigation.related ? <EmptyState icon="link" title={t("inbox.related")} description={t("inbox.relatedHint")} /> : <>
      <div className="space-y-3 border-b border-border-subtle p-4">
        <div className="text-13 font-semibold">{kind === "file" ? part?.name || part?.file?.filename || t("inbox.file") : t("inbox.sharedText")}</div>
        {kind === "fragment" ? <p className="line-clamp-4 whitespace-pre-wrap text-13 text-fg-muted">{part?.fragment?.text}</p> : null}
        <p className="text-2xs text-fg-muted">{t("inbox.relatedCount", { count: page?.message_count ?? "…" })}</p>
        {navigation.relatedFrom ? <Button size="sm" variant="ghost" onClick={() => navigation.read(navigation.relatedFrom)}><Glyph name="chevron-left" />{t("inbox.returnSource")}</Button> : null}
        <DebouncedSearchInput size="sm" aria-label={t("inbox.searchRelated")} placeholder={t("inbox.searchRelated")} value={view.filter.textTerm("text")} onValueChange={view.setText} />
        <Select size="sm" aria-label={t("inbox.recent")} value={view.state.sorting?.[0]?.desc === false ? "oldest" : "recent"} onValueChange={value => view.setSorting([{ id: "latest", desc: value !== "oldest" }])} options={["recent", "oldest"].map(value => ({ value, label: t(`inbox.${value}`) }))} />
      </div>
      {error ? <Alert tone="danger">{error.message}</Alert> : query.isPending ? <LoadingPanel /> : <TimelineView className="min-h-0 flex-1 overflow-auto p-4" rows={page?.rows ?? []} dateField="created_at" rowKey="id" emptyContent={<EmptyState title={t("inbox.noRelated")} />}
        renderEntry={message => <article className="space-y-2"><div className="flex items-center gap-2"><Tag>{message.platform}</Tag>{message.id === navigation.relatedFrom ? <Tag tone="brand">{t("inbox.source")}</Tag> : null}</div>
          <div className="truncate text-13 font-medium">{senderDisplayName(message.sender, t("inbox.unknownSender"))}</div>
          <div className="line-clamp-3 text-13 text-fg-muted">{message.preview}</div><div className="text-2xs text-fg-muted">{message.channel?.display_name} · <RelativeTime value={message.sent_at ?? message.created_at} /></div>
          <Button size="sm" variant={message.id === navigation.message ? "secondary" : "ghost"} onClick={() => navigation.read(message.id, message.parts.find(part => kind === "file" ? part.file?.id === id : part.fragment?.id === id)?.id)}>{t("inbox.read")}<Glyph name="chevron-right" /></Button>
          {message.thread ? <Button size="sm" variant="ghost" onClick={() => navigation.conversation(message.thread!.id, message.id)}>{t("inbox.openConversation")}</Button> : null}
        </article>} />}
      <div className="flex items-center gap-1 border-t border-border-subtle p-3"><Pager page={view.state.pagination.pageIndex + 1} pageSize={PAGE_SIZE} total={page?.count} onPageChange={view.setPage} labelElement="span" disabled={query.isFetching} /></div>
    </>}
  </section>;
}
