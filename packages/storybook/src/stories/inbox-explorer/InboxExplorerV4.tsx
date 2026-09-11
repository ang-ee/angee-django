import * as React from "react";
import { Filter } from "@angee/metadata";
import {
  Alert, Avatar, Button, Collapsible, ConsoleLayout, ControlBand, EmptyState,
  Glyph, MessageAttachmentChip, MessageDaySeparator, MessageFeed, MessagePartsView,
  MessageRow, MetaGrid, PageAside, PageHeader, PageToolbar, PopoverContent,
  PopoverPortal, PopoverPositioner, PopoverRoot, PopoverTitle, PopoverTrigger,
  PrimaryPanePublisher, ReactionBar, RemovableChip, ResourceList, ResourceViewProvider,
  SearchInput, SectionEyebrow, Select, StatusSegment, Statusline, StatuslineSpacer,
  Tag, TimelineView, TreeView, Checkbox, avatarInitials, cn,
  useBreadcrumbLeafLabel, useChatter, useChatterContent, useResourceView,
  type ChatterContent,
} from "@angee/ui";

import { RuntimeRegistryFixture } from "../runtime-fixtures";
import { Related } from "./Related";
import { accountById, circles, fragments, platformLabel, senderById,
  senderName, snippet, type Message } from "./fixtures";
import { senderQuery, type Coverage } from "./v2-model";
import { accountOptions, activityRows, allCoverage, conversationRows, covered,
  matchedMessages, messageConversation, sampleMessages, scopeName, targetUses,
  type ConversationRow } from "./v3-model";

// V4 is a bounded, desktop composition study. The shared shell owns all panes;
// the existing fixture projection stands in for Nexus reads. Navigation is deferred.
export interface InboxExplorerV4Props {
  sender?: string;
  message?: string;
  thread?: string;
  search?: string;
  coverage?: Partial<Coverage>;
  related?: { target: string; origin: string };
}

const RELATED = "nexus-related";
// Adapt the fixture timeline to ResourceList's collection slot without a second
// collection controller. Production supplies Messaging's query-backed renderer.
const CollectionContent = React.createContext<React.ReactNode>(null);
function FixtureCollection() { return <>{React.useContext(CollectionContent)}</>; }
const shortDate = (value: string) => new Date(value).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const clockTime = (value: string) => new Date(value).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

export function InboxExplorerV4(props: InboxExplorerV4Props) {
  return <RuntimeRegistryFixture>
    <ConsoleLayout className="h-dvh min-h-0 w-full">
      <ResourceViewProvider scope="local" initialState={{ filter: props.search ? { title: { iContains: props.search } } : {} }}>
        <Explorer {...props} />
      </ResourceViewProvider>
    </ConsoleLayout>
  </RuntimeRegistryFixture>;
}

function Explorer(initial: InboxExplorerV4Props) {
  useBreadcrumbLeafLabel("Inbox");
  const collection = useResourceView();
  const { setActiveTab, setCollapsed } = useChatter();
  const [sender, setSender] = React.useState(initial.sender ?? "pty_anna");
  const [circle, setCircle] = React.useState("all");
  const [messageId, setMessage] = React.useState(initial.thread ? "" : initial.message ?? "");
  const [thread, setThread] = React.useState(initial.thread ?? "");
  const [coverage, setCoverage] = React.useState<Coverage>({ ...allCoverage, ...initial.coverage });
  const [quoted, setQuoted] = React.useState(false);
  const [related, setRelated] = React.useState(initial.related ?? null);
  const text = Filter.from(collection.state.filter).textTerm("title");
  const hasFiles = Boolean(collection.state.filter.hasFiles);
  const selected = sampleMessages.find(message => message.id === messageId);
  const matches = matchedMessages(coverage, sender, circle, collection.state.filter, quoted);
  const rows = conversationRows(matches, quoted);
  const matchIds = new Set(matches.map(message => message.id));
  const scope = scopeName(sender, circle);
  const threadRows = sampleMessages.filter(message => message.threadId === thread)
    .sort((a, b) => a.sentAt.localeCompare(b.sentAt));

  const reveal = React.useCallback(() => { setActiveTab(RELATED); setCollapsed(false); }, [setActiveTab, setCollapsed]);
  React.useEffect(() => {
    // The shell registers its pane controller after its children mount.
    const frame = requestAnimationFrame(() => {
      setActiveTab(RELATED); setCollapsed(!related);
    });
    return () => cancelAnimationFrame(frame);
  }, [related, setActiveTab, setCollapsed]);
  const selectScope = React.useCallback((id: string, circleId = "all") => {
    setSender(id); setCircle(circleId); setThread(""); setMessage("");
  }, []);
  const read = React.useCallback((message: Message) => {
    setMessage(message.id);
  }, []);
  const openThread = React.useCallback((message: Message) => {
    setThread(message.threadId); setMessage("");
  }, []);
  const connect = React.useCallback((target: string, origin: string) => {
    setRelated({ target, origin }); reveal();
  }, [reveal]);
  const closeMessage = React.useCallback(() => { setMessage(""); }, []);
  const showResults = React.useCallback(() => { setThread(""); setMessage(""); }, []);
  const closeRelated = React.useCallback(() => { setRelated(null); setCollapsed(true); }, [setCollapsed]);
  const primary = React.useMemo(() => <SenderNavigator coverage={coverage} sender={sender} circle={circle} onSelect={selectScope} />,
    [coverage, sender, circle, selectScope]);
  const outside = Boolean(selected && !matchIds.has(selected.id));
  const inspector = React.useMemo<ChatterContent>(() => ({ tabs: [{
    id: RELATED, label: "Related", panelClassName: "p-0",
    children: <PageAside collapse="never" gutter="none" className="w-full min-w-0 border-0 bg-sheet-2 [contain:inline-size]">
      {related ? <Related key={`${related.target}:${related.origin}`} target={related.target} origin={related.origin}
        selected={messageId} onSelect={read} onThread={openThread} onClose={closeRelated} />
        : <EmptyState icon="link" title="Follow a connection" description="Choose a file or shared text to see its other message uses." />}
    </PageAside>,
  }] }), [related, messageId, read, openThread, closeRelated]);
  useChatterContent(inspector);

  function setSearch(value: string) {
    const { title: _title, ...rest } = collection.state.filter;
    collection.setFilter(value ? { ...rest, title: { iContains: value } } : rest);
  }
  function setFiles(value: boolean) {
    const { hasFiles: _hasFiles, ...rest } = collection.state.filter;
    collection.setFilter(value ? { ...rest, hasFiles: { exact: true } } : rest);
  }
  const coverageChips = [
    { key: "platform", label: platformLabel[coverage.platform as keyof typeof platformLabel] },
    { key: "account", label: accountById.get(coverage.account)?.label },
    { key: "kind", label: { direct: "Direct messages", group: "Group conversations", mail: "Mail" }[coverage.kind] },
    { key: "period", label: `Last ${coverage.period} days` },
  ].filter(item => coverage[item.key as keyof Coverage] !== "all");

  return <div className="flex h-full min-h-0 min-w-0 flex-col">
    <PrimaryPanePublisher node={primary} />
    <ControlBand>
      <SearchInput className="max-w-xl flex-1" size="sm" surface="inset" aria-label="Search messages"
        placeholder={sender ? `Search exchanges with ${scope}…` : "Search all messages…"}
        value={text} onChange={event => setSearch(event.target.value)} onClear={() => setSearch("")} />
      <CoverageMenu coverage={coverage} onChange={setCoverage} hasFiles={hasFiles} onFiles={setFiles} quoted={quoted} onQuoted={setQuoted} />
      <span className="ml-auto flex items-center gap-2 whitespace-nowrap text-2xs text-fg-muted"><Glyph name="arrow-down" size={14} />Latest activity</span>
      {related ? <Button size="sm" variant="secondary" onClick={reveal}>
        <Glyph name="link" />Related <Tag tone="brand">{targetUses(related.target).length}</Tag>
      </Button> : null}
    </ControlBand>
    <CollectionContent.Provider value={
    <section aria-label="Conversation results" className="flex h-full min-h-0 flex-col">
    <PageHeader density="compact" title={thread ? threadRows[0] ? messageConversation(threadRows[0]) : "Conversation unavailable" : sender || circle !== "all" ? scope : "All conversations"}
      description={thread ? `${threadRows.length} messages · full conversation` : `${matches.length} matching messages in ${rows.length} conversations`}
      actions={thread ? <Button size="sm" variant="secondary" onClick={showResults}><Glyph name="chevron-left" />Back to results</Button>
        : sender || circle !== "all" ? <Button size="sm" variant="ghost" onClick={() => selectScope("")}>Everyone<Glyph name="x" /></Button> : null} />
    {coverageChips.length || hasFiles || quoted ? <PageToolbar density="compact" start={<>
      {coverageChips.map(item => <RemovableChip key={item.key} removeLabel={`Clear ${item.key}`} onRemove={() => setCoverage({ ...coverage, [item.key]: "all" })}>{item.label}</RemovableChip>)}
      {hasFiles ? <RemovableChip removeLabel="Clear attachment filter" onRemove={() => setFiles(false)}>Has attachment</RemovableChip> : null}
      {quoted ? <RemovableChip removeLabel="Exclude quoted text" onRemove={() => setQuoted(false)}>Including quoted text</RemovableChip> : null}
    </>} /> : null}
    {thread ? <div className="min-h-0 flex-1 overflow-auto p-6">
      <div className="mx-auto max-w-3xl">
        {text ? <p className="mb-5 text-13 text-fg-muted">Matches for “{text}” are marked; the full conversation stays readable.</p> : null}
        <MessageFeed label="Full conversation">{threadRows.map((message, index) => <React.Fragment key={message.id}>
          {index === 0 || shortDate(threadRows[index - 1]!.sentAt) !== shortDate(message.sentAt)
            ? <MessageDaySeparator>{shortDate(message.sentAt)}</MessageDaySeparator> : null}
          <MessageRow avatar={<Avatar size="md" initials={avatarInitials(senderName(message.senderId))} />}
            author={senderName(message.senderId)} timestamp={message.sentAt}
            channel={text ? <Tag tone={matchIds.has(message.id) ? "brand" : "neutral"}>{matchIds.has(message.id) ? "Match" : "Context"}</Tag> : null}
            className={cn("rounded-8 p-3", message.id === messageId && "bg-brand-soft")}
            actions={<Button size="sm" variant="ghost" onClick={() => read(message)}>Read message<Glyph name="chevron-right" /></Button>}>
            <MessageContent message={message} onRelated={connect} />
          </MessageRow>
        </React.Fragment>)}</MessageFeed>
      </div>
    </div> : <TimelineView className="min-h-0 p-5" rows={rows} dateField="latest" rowKey="id"
      renderEntry={row => <ConversationPreview row={row} selected={messageId} onRead={read} onThread={openThread} />}
      emptyContent={<EmptyState icon="search" title="No matching conversations" description="Try another search or adjust coverage. Your open message and Related target are kept."
        actions={<><Button size="sm" variant="secondary" onClick={() => collection.setFilter({})}>Clear message filters</Button>
          <Button size="sm" variant="ghost" onClick={() => setCoverage(allCoverage)}>Clear coverage</Button></>} />} />}
    </section>
    }>
      <ResourceList resource="messaging.Message" columns={[{ field: "title" }]} list={FixtureCollection}
        className="min-h-0 flex-1 overflow-auto" placement="inline" hideCreate recordId={messageId || null}
        onSelect={id => setMessage(id ?? "")} onClose={closeMessage}
        renderRecord={() => selected ? <MessageReader message={selected} outside={outside} target={related?.target}
          scope={scope} backLabel={thread ? "Back to conversation" : "Back to results"}
          onRelated={connect} onThread={openThread} onSender={selectScope} onBack={closeMessage} onResults={showResults} />
          : <EmptyState icon="comments" title="Message unavailable" actions={<Button size="sm" onClick={closeMessage}>Back to results</Button>} />} />
    </CollectionContent.Provider>
    <Statusline><StatusSegment icon="comments">{scope}</StatusSegment><StatusSegment>{coverageChips.length ? "Filtered coverage" : "All accounts"}</StatusSegment>
      <StatuslineSpacer /><StatusSegment>23 sample messages</StatusSegment><StatusSegment>V4 · desktop study</StatusSegment></Statusline>
  </div>;
}

function SenderNavigator({ coverage, sender, circle, onSelect }: {
  coverage: Coverage; sender: string; circle: string; onSelect: (sender: string, circle?: string) => void;
}) {
  const [search, setSearch] = React.useState("");
  const [mine, setMine] = React.useState(false);
  const activity = activityRows(coverage, mine).filter(row => senderQuery.matches(row, { title: { iContains: search } }))
    .sort((a, b) => b.latest.localeCompare(a.latest));
  return <section aria-label="Sender explorer" className="flex h-full min-h-0 flex-col">
    <PageHeader density="compact" headingLevel={2} title="Senders" />
    <div className="space-y-4 p-3">
      <SearchInput size="sm" surface="inset" aria-label="Find sender" placeholder="Find a sender…" value={search}
        onChange={event => setSearch(event.target.value)} onClear={() => setSearch("")} />
      <TreeView rows={[{ id: "all", name: "Everyone", count: covered(coverage).length, icon: "comments" }]}
        badge="count" icon="icon" selectedId={!sender && circle === "all" ? "all" : undefined} onSelect={() => onSelect("")} />
      <SectionEyebrow>Recent activity</SectionEyebrow>
      <TreeView rows={activity} badge="count" selectedId={sender} onSelect={row => onSelect(row.id)}
        renderRow={row => <span className="flex min-w-0 items-center gap-2" title={row.address}>
          <Avatar size="sm" initials={avatarInitials(row.name)} /><span className="truncate">{row.name}</span>
          {row.link === "suggested" ? <Glyph name="link" size={12} /> : null}
        </span>} emptyContent="No matching senders" />
      <label className="flex items-center gap-2 text-2xs text-fg-muted"><Checkbox size="sm" checked={mine} onCheckedChange={setMine} />Include my sent</label>
    </div>
    <div className="space-y-3 border-t border-border-subtle p-3">
      <SectionEyebrow>Circles</SectionEyebrow>
      <TreeView rows={circles.map(item => ({ ...item, icon: "users" }))} parent="parent" icon="icon"
        selectedId={circle} onSelect={row => onSelect("", row.id)} />
    </div>
    <div className="mt-auto p-4 text-2xs leading-relaxed text-fg-muted">{activity.length} senders in coverage</div>
  </section>;
}

function CoverageMenu({ coverage, onChange, hasFiles, onFiles, quoted, onQuoted }: {
  coverage: Coverage; onChange: (next: Coverage) => void; hasFiles: boolean; onFiles: (value: boolean) => void;
  quoted: boolean; onQuoted: (value: boolean) => void;
}) {
  const count = Object.values(coverage).filter(value => value !== "all").length + Number(hasFiles) + Number(quoted);
  return <PopoverRoot><PopoverTrigger render={<Button size="sm" variant="secondary" />}><Glyph name="filter" />Filters{count ? <Tag>{count}</Tag> : null}</PopoverTrigger>
    <PopoverPortal><PopoverPositioner sideOffset={8} align="end"><PopoverContent className="w-80">
      <PopoverTitle>Search coverage</PopoverTitle>
      <div className="space-y-4 p-4 pt-1">
        <label className="block space-y-1 text-13">Platform<Select size="sm" aria-label="Platform" value={coverage.platform}
          onValueChange={platform => onChange({ ...coverage, platform })} options={[{ value: "all", label: "All platforms" }, ...Object.entries(platformLabel).map(([value, label]) => ({ value, label }))]} /></label>
        <label className="block space-y-1 text-13">Account<Select size="sm" aria-label="Account" value={coverage.account}
          onValueChange={account => onChange({ ...coverage, account })} options={accountOptions} /></label>
        <label className="block space-y-1 text-13">Conversation<Select size="sm" aria-label="Conversation kind" value={coverage.kind}
          onValueChange={kind => onChange({ ...coverage, kind })} options={[{ value: "all", label: "All kinds" }, { value: "direct", label: "Direct messages" }, { value: "group", label: "Group conversations" }, { value: "mail", label: "Mail" }]} /></label>
        <label className="block space-y-1 text-13">Period<Select size="sm" aria-label="Period" value={coverage.period}
          onValueChange={period => onChange({ ...coverage, period })} options={[{ value: "all", label: "All time" }, { value: "1", label: "Last 24 hours" }, { value: "7", label: "Last 7 days" }, { value: "30", label: "Last 30 days" }]} /></label>
        <label className="flex items-center gap-2 text-13"><Checkbox size="sm" checked={hasFiles} onCheckedChange={onFiles} />Has attachment</label>
        <label className="flex items-center gap-2 text-13"><Checkbox size="sm" checked={quoted} onCheckedChange={onQuoted} />Include quotes & signatures</label>
        <Button size="sm" variant="ghost" onClick={() => onChange(allCoverage)}>Clear coverage</Button>
      </div>
    </PopoverContent></PopoverPositioner></PopoverPortal>
  </PopoverRoot>;
}

function ConversationPreview({ row, selected, onRead, onThread }: {
  row: ConversationRow; selected: string; onRead: (message: Message) => void; onThread: (message: Message) => void;
}) {
  const newest = row.hits[0]!;
  const account = accountById.get(newest.accountId)!;
  return <article aria-label={row.label}>
    <div className="mb-3 flex items-center gap-2">
      <Glyph name={row.kind === "mail" ? "file" : row.kind === "group" ? "users" : "comments"} size={16} className="shrink-0 text-fg-muted" />
      <Button size="sm" variant="ghost" className="min-w-0 justify-start px-0 text-left font-semibold" onClick={() => row.threadId ? onThread(newest) : onRead(newest)}>
        <span className="truncate">{row.label}</span><Glyph name="chevron-right" size={14} />
      </Button>
      <Tag className="ml-auto shrink-0">{platformLabel[account.platform]}</Tag>
    </div>
    <div className="space-y-1">{row.preview.map(message => <Button key={message.id} variant="ghost"
      aria-label={`Read ${senderName(message.senderId)}: ${snippet(message)}`} aria-pressed={selected === message.id}
      onClick={() => onRead(message)} style={{ height: "auto" }}
      className={cn("w-full items-start justify-start gap-3 whitespace-normal rounded-6 px-3 py-2.5 text-left font-normal", selected === message.id && "bg-brand-soft ring-1 ring-inset ring-brand/25")}>
      <Avatar size="sm" initials={avatarInitials(senderName(message.senderId))} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-13"><span className="font-medium">{senderName(message.senderId)}</span>
          <time className="ml-auto text-2xs text-fg-muted" dateTime={message.sentAt}>{clockTime(message.sentAt)}</time></span>
        <span className="mt-1 block line-clamp-2 text-13 leading-relaxed text-fg-2">{message.body}</span>
        {message.attachments?.length ? <span className="mt-2 flex items-center gap-1 text-2xs text-fg-muted"><Glyph name="file" size={12} />
          <span className="truncate">{message.attachments.map(file => file.name).join(" · ")}</span></span> : null}
      </span>
    </Button>)}</div>
    <div className="mt-3 flex items-center justify-between gap-2 text-2xs text-fg-muted">
      <span>{row.hits.length} matching · {row.total} in conversation</span>
      <span>{shortDate(newest.sentAt)}</span>
    </div>
  </article>;
}

function MessageReader({ message, outside, target, scope, backLabel, onRelated, onThread, onSender, onBack, onResults }: {
  message: Message; outside: boolean; target?: string; onRelated: (target: string, origin: string) => void;
  scope: string; backLabel: string; onThread: (message: Message) => void; onSender: (sender: string) => void; onBack: () => void; onResults: () => void;
}) {
  const sender = senderById.get(message.senderId);
  const account = accountById.get(message.accountId)!;
  return <section aria-label="Message reader" className="flex h-full min-h-0 flex-col">
    <PageHeader density="compact" title={message.subject || `Message from ${senderName(message.senderId)}`}
      crumbs={<nav aria-label="Message breadcrumbs" className="flex min-w-0 items-center gap-1">
        <Button size="sm" variant="ghost" className="min-w-0 px-0" onClick={onResults}><span className="truncate">Results · {scope}</span></Button>
        <Glyph name="chevron-right" size={12} />
        {message.threadId ? <><Button size="sm" variant="ghost" className="min-w-0 px-0" onClick={() => onThread(message)}><span className="truncate">{messageConversation(message)}</span></Button><Glyph name="chevron-right" size={12} /></> : null}
        <span aria-current="page">Message</span>
      </nav>}
      actions={<Button size="sm" variant="secondary" onClick={onBack}><Glyph name="chevron-left" />{backLabel}</Button>} />
    <div className="min-h-0 flex-1 overflow-auto">
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div className="flex items-center gap-3"><Avatar size="lg" initials={avatarInitials(senderName(message.senderId))} />
        <div className="min-w-0"><div className="font-semibold text-13">{senderName(message.senderId)}</div>
          <div className="truncate text-2xs text-fg-muted">{sender?.link === "confirmed" ? "Confirmed identity" : sender?.address ?? "Unresolved sender"}</div></div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-2xs text-fg-muted"><Tag>{platformLabel[account.platform]}</Tag>
        <time dateTime={message.sentAt}>{shortDate(message.sentAt)} · {clockTime(message.sentAt)}</time></div>
      {outside ? <Alert tone="info" format="banner">Outside current results</Alert> : null}
      {sender?.link === "suggested" ? <Alert tone="info" format="banner">Suggested identity: {senderName(sender.candidate!)}. Still a separate handle.</Alert> : null}
      <MessageContent key={message.id} message={message} target={target} onRelated={onRelated} />
      <div className="border-t border-border-subtle pt-4">
        {message.threadId ? <Button size="sm" variant="secondary" className="w-full" onClick={() => onThread(message)}>Open conversation<Glyph name="chevron-right" /></Button> : <Tag>Standalone message</Tag>}
      </div>
      <Collapsible.Root><Collapsible.Trigger className="text-13 font-medium">Message details</Collapsible.Trigger>
        <Collapsible.Panel className="space-y-3 pt-3"><MetaGrid rows={[["Account", account.label], ["Conversation", messageConversation(message)],
          ["Direction", message.direction], ["Identity", sender?.link ?? "Unresolved"]]} />
          {sender ? <Button size="sm" variant="ghost" onClick={() => onSender(sender.id)}>Explore sender<Glyph name="chevron-right" /></Button> : null}
        </Collapsible.Panel>
      </Collapsible.Root>
    </div>
    </div>
  </section>;
}

function MessageContent({ message, target, onRelated }: { message: Message; target?: string; onRelated: (target: string, origin: string) => void }) {
  return <div className="space-y-4 whitespace-normal">
    <MessagePartsView parts={[{ role: "TITLE", fragment: { text: message.subject } }, { role: "BODY", fragment: { text: message.body } },
      { role: "QUOTED", fragment: { text: message.quoted } }]} resolveFileUrl={() => null} />
    {message.signature ? <Collapsible.Root><Collapsible.Trigger>Signature</Collapsible.Trigger><Collapsible.Panel>
      <MessagePartsView parts={[{ role: "SIGNATURE", fragment: { text: message.signature } }]} resolveFileUrl={() => null} />
    </Collapsible.Panel></Collapsible.Root> : null}
    {message.attachments?.length ? <div className="space-y-3"><SectionEyebrow>Attachments</SectionEyebrow>
      {message.attachments.map(file => <div key={file.fileId} className={cn("space-y-2 rounded-8 border border-border-subtle bg-sheet p-3", target === `file:${file.fileId}` && "border-brand bg-brand-soft")}>
        <MessageAttachmentChip icon={<Glyph name="file" />} onClick={() => onRelated(`file:${file.fileId}`, message.id)}>{file.name}</MessageAttachmentChip>
        <div className="text-2xs text-fg-muted">{file.size} · {file.mime === "application/pdf" ? "PDF document" : file.mime}</div>
        <Button size="sm" variant="ghost" className="h-auto px-0 text-brand" onClick={() => onRelated(`file:${file.fileId}`, message.id)}>
          <Glyph name="link" size={13} />{targetUses(`file:${file.fileId}`).length} message uses<Glyph name="chevron-right" size={13} />
        </Button>
      </div>)}
    </div> : null}
    {message.fragments?.map(id => <div key={id} className={cn("space-y-2 border-l-2 border-border-subtle pl-3", target === `fragment:${id}` && "border-brand bg-brand-soft")}>
      <p className="line-clamp-3 text-13 leading-relaxed text-fg-muted">{fragments[id]}</p>
      <Button size="sm" variant="ghost" className="h-auto px-0 text-brand" onClick={() => onRelated(`fragment:${id}`, message.id)}><Glyph name="link" size={13} />Same text in {targetUses(`fragment:${id}`).length} messages</Button>
    </div>)}
    {message.reactions ? <ReactionBar reactions={[...message.reactions]} /> : null}
  </div>;
}

