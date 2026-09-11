import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { ThreadTranscript } from "@angee/messaging";
import { senderDisplayName } from "@angee/parties";
import { Alert, Avatar, Button, ControlBand, EmptyState, Glyph, LoadingPanel, PageAside, PageHeader, PageToolbar, Pager, PrimaryPanePublisher, RelativeTime, RemovableChip, ResourceList, ResourceViewProvider, DebouncedSearchInput, Select, StatusSegment, Statusline, StatuslineSpacer, Tag, TimelineView, avatarInitials, useBreadcrumbLeafLabel, useChatter, useChatterContent, type ChatterContent } from "@angee/ui";
import { InboxConversations, type InboxConversationRow } from "./inbox/documents";
import { InboxFilters } from "./inbox/Filters";
import { InboxMessageReader } from "./inbox/MessageReader";
import { InboxRelatedPane } from "./inbox/Related";
import { InboxSendersPane } from "./inbox/Senders";
import { COLLECTION_INITIAL, INBOX_MODELS, PAGE_SIZE, RELATED_TAB, inboxQuery, useInboxFilter, useInboxNavigation, type InboxNavigation } from "./inbox/state";
import { useNexusT } from "./i18n";

// ResourceList owns the collection → inline record transition; its list slot
// receives the server-paged conversation projection through this adapter.
const CollectionContent = createContext<ReactNode>(null);
function ConversationCollection() { return <>{useContext(CollectionContent)}</>; }

/** ConsoleLayout is supplied by the registered route, so this page only publishes panes. */
export function InboxPage() {
  return <ResourceViewProvider namespace="c" initialState={COLLECTION_INITIAL}><InboxExplorer /></ResourceViewProvider>;
}

function InboxExplorer() {
  const t = useNexusT();
  useBreadcrumbLeafLabel(t("inbox.title"));
  const navigation = useInboxNavigation();
  const view = useInboxFilter();
  const { setActiveTab, setCollapsed } = useChatter();
  const scope = useMemo(() => ({ ...inboxQuery(view.state.filter), sender: navigation.sender, circle: navigation.circle }), [view.state.filter, navigation.sender, navigation.circle]);
  const query = useAuthoredQuery(InboxConversations, { ...scope, page: view.state.pagination.pageIndex + 1, oldest: view.state.sorting?.[0]?.desc === false }, { models: INBOX_MODELS, enabled: !view.state.queryError });
  const page = query.data?.inbox_conversations;
  const primary = useMemo(() => <ResourceViewProvider namespace="s" initialState={COLLECTION_INITIAL}><InboxSendersPane scope={scope} navigation={navigation} /></ResourceViewProvider>, [scope, navigation]);
  const related = useMemo<ChatterContent>(() => ({ tabs: [{ id: RELATED_TAB, label: t("inbox.related"), icon: "link", panelClassName: "p-0",
    children: <PageAside collapse="never" gutter="none" className="h-full w-full min-w-0 border-0 bg-sheet-2"><ResourceViewProvider namespace="r" initialState={COLLECTION_INITIAL}><InboxRelatedPane navigation={navigation} /></ResourceViewProvider></PageAside> }] }), [navigation, t]);
  useChatterContent(related);
  useEffect(() => {
    const frame = requestAnimationFrame(() => { setActiveTab(RELATED_TAB); setCollapsed(!navigation.related); });
    return () => cancelAnimationFrame(frame);
  }, [navigation.related, setActiveTab, setCollapsed]);
  const error = view.state.queryError ?? query.error;
  return <div className="flex h-full min-h-0 min-w-0 flex-col">
    <PrimaryPanePublisher node={primary} />
    <ControlBand>
      <DebouncedSearchInput size="sm" surface="inset" className="max-w-xl flex-1" aria-label={t("inbox.search")} placeholder={t("inbox.search")} value={view.filter.textTerm("text")} onValueChange={view.setText} />
      <InboxFilters />
      <Select size="sm" className="ml-auto w-40" aria-label={t("inbox.recent")} value={view.state.sorting?.[0]?.desc === false ? "oldest" : "recent"} onValueChange={value => view.setSorting([{ id: "latest", desc: value !== "oldest" }])} options={["recent", "oldest"].map(value => ({ value, label: t(`inbox.${value}`) }))} />
      {navigation.related ? <Button size="sm" variant="secondary" onClick={() => { setActiveTab(RELATED_TAB); setCollapsed(false); }}><Glyph name="link" />{t("inbox.related")}</Button> : null}
    </ControlBand>
    {view.filter.hasEntries() ? <PageToolbar density="compact" start={<>
      {Object.keys(view.state.filter).filter(field => field !== "text").map(field => <RemovableChip key={field} removeLabel={t("inbox.clear")} onRemove={() => view.setFilter(view.filter.withoutFields([field]))}>{t(`inbox.${field}`)}: {view.filter.facetValues(field).join(", ")}</RemovableChip>)}
      <Button size="sm" variant="ghost" onClick={() => view.setFilter({})}>{t("inbox.clear")}</Button>
    </>} /> : null}
    <CollectionContent.Provider value={<section aria-label={t("inbox.results")} className="flex h-full min-h-0 flex-col">
      <PageHeader density="compact" title={navigation.thread ? t("inbox.conversation") : navigation.sender ? t("inbox.results") : t("inbox.conversations")}
        description={t("inbox.summary", { messages: page?.message_count ?? "…", conversations: page?.count ?? "…" })}
        actions={navigation.thread ? <Button size="sm" variant="secondary" onClick={navigation.results}><Glyph name="chevron-left" />{t("inbox.backResults")}</Button> : navigation.sender || navigation.circle ? <Button size="sm" variant="ghost" onClick={() => navigation.select()}>{t("inbox.everyone")}<Glyph name="x" /></Button> : null} />
      {error ? <Alert tone="danger">{error.message}<Button size="sm" onClick={() => void query.refetch()}>{t("inbox.retry")}</Button></Alert> : navigation.thread ? <ThreadTranscript threadId={navigation.thread} anchor={navigation.at} onAnchorChange={at => navigation.patch({ at: at || undefined })} renderMessageActions={message => <Button size="sm" variant="ghost" onClick={() => navigation.read(message.id)}>{t("inbox.read")}<Glyph name="chevron-right" /></Button>} /> : query.isPending ? <LoadingPanel message={t("inbox.loading")} /> : <>
        <TimelineView className="min-h-0 flex-1 overflow-auto p-5" rows={page?.rows ?? []} dateField="latest" rowKey="id"
          renderEntry={row => <ConversationPreview row={row} navigation={navigation} />}
          emptyContent={<EmptyState icon="search" title={t("inbox.noResults")} description={t("inbox.noResultsHint")} actions={<Button size="sm" variant="secondary" onClick={() => view.setFilter({})}>{t("inbox.clear")}</Button>} />} />
        <div className="flex items-center justify-end gap-1 border-t border-border-subtle px-5 py-2"><Pager page={view.state.pagination.pageIndex + 1} pageSize={PAGE_SIZE} total={page?.count} onPageChange={view.setPage} labelElement="span" disabled={query.isFetching} /></div>
      </>}
    </section>}>
      <ResourceList resource="messaging.Message" columns={[{ field: "title" }]} list={ConversationCollection} placement="inline" hideCreate recordId={navigation.message || null}
        onSelect={id => id ? navigation.read(id) : navigation.back()} onClose={navigation.back}
        className="min-h-0 flex-1 overflow-hidden" renderRecord={() => <InboxMessageReader key={navigation.message} scope={scope} navigation={navigation} />} />
    </CollectionContent.Provider>
    <Statusline><StatusSegment icon="comments">{t("inbox.title")}</StatusSegment><StatuslineSpacer /><StatusSegment>{t("inbox.summary", { messages: page?.message_count ?? "…", conversations: page?.count ?? "…" })}</StatusSegment></Statusline>
  </div>;
}

function ConversationPreview({ row, navigation }: { row: InboxConversationRow; navigation: InboxNavigation }) {
  const t = useNexusT();
  const newest = row.messages[0];
  return <article className="space-y-2">
    <div className="flex items-center gap-2"><Glyph name="comments" className="text-fg-muted" /><Button size="sm" variant="ghost" className="min-w-0 justify-start px-0 text-left font-semibold" onClick={() => row.thread ? navigation.conversation(row.thread.id, newest?.id) : newest && navigation.read(newest.id)}>
      <span className="truncate">{row.thread?.title?.text || newest?.title || senderDisplayName(newest?.sender, t("inbox.conversation"))}</span><Glyph name="chevron-right" size={14} /></Button><Tag className="ml-auto">{newest?.platform}</Tag></div>
    {row.messages.map(message => <Button key={message.id} variant="ghost" style={{ height: "auto" }} className="w-full items-start justify-start gap-3 whitespace-normal rounded-6 px-3 py-2.5 text-left font-normal" aria-label={`${t("inbox.read")}: ${message.title || message.preview}`} onClick={() => navigation.read(message.id)}>
      <Avatar size="sm" initials={avatarInitials(senderDisplayName(message.sender, t("inbox.unknownSender")))} /><span className="min-w-0 flex-1"><span className="flex items-center gap-2"><span className="truncate font-medium">{senderDisplayName(message.sender, t("inbox.unknownSender"))}</span><span className="ml-auto text-2xs text-fg-muted"><RelativeTime value={message.sent_at ?? message.created_at} /></span></span>
        <span className="line-clamp-2 text-13 text-fg-muted">{message.preview}</span><span className="text-2xs text-fg-muted">{message.channel?.display_name}</span></span>
    </Button>)}
    <div className="pl-3 text-2xs text-fg-muted">{t("inbox.matched", { matching: row.matching_count, total: row.total_count })}</div>
  </article>;
}
