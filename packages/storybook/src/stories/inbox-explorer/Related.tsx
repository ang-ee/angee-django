import * as React from "react";
import { Button, ControlBandProvider, Glyph, PageToolbar, RelativeTime, ResourceViewProvider,
  RowsListView, Select, SurfaceHeader, Tag, useResourceView, type ListColumn } from "@angee/ui";
import { accountById, fragments, platformLabel, senderName, snippet, type Message } from "./fixtures";
import { messageConversation, messageProjection, sampleMessages, sampleQuery, targetUses, type MessageProjection } from "./v3-model";

// Shared fixture panel, retained from V3. Related's collection owns its own query.
export function CollectionOrder({ label }: { label: string }) {
  const { state, setSorting } = useResourceView();
  return <Select aria-label={label} size="sm" className="w-32" value={state.sorting?.[0]?.desc === false ? "oldest" : "latest"}
    onValueChange={value => setSorting([{ id: "latest", desc: value === "latest" }])}
    options={[{ value: "latest", label: "Latest match" }, { value: "oldest", label: "Oldest match" }]} />;
}

export function Related({ target, origin, selected, className = "", onSelect, onThread, onClose }: {
  target: string; origin: string; selected: string; className?: string; onSelect: (message: Message) => void;
  onThread: (message: Message) => void; onClose: () => void;
}) {
  const rows = React.useMemo(() => targetUses(target).map(message => messageProjection(message)), [target]);
  const [kind, id] = target.split(":");
  const source = sampleMessages.find(message => message.id === origin);
  const name = source?.attachments?.find(file => file.fileId === id)?.name ?? rows.flatMap(row => row.message.attachments ?? []).find(file => file.fileId === id)?.name;
  const columns: readonly ListColumn<MessageProjection>[] = [{ field: "title", header: "Message use", sortable: false, render: ({ message }) => <div className="space-y-1 py-1">
    <div className="font-medium">{senderName(message.senderId)}</div>
    <div className="text-2xs text-fg-muted">{messageConversation(message)} · {platformLabel[accountById.get(message.accountId)!.platform]}</div>
    <div className="whitespace-normal text-13">{kind === "file" ? message.attachments?.find(file => file.fileId === id)?.name : snippet(message)}</div>
    <RelativeTime value={message.sentAt} className="text-2xs text-fg-muted" />
    {message.id === origin ? <Tag>Starting message</Tag> : null}
    <div className="flex flex-wrap gap-1"><Button size="sm" variant="secondary" onClick={() => onSelect(message)}>Read message</Button>
      {message.threadId ? <Button size="sm" variant="ghost" onClick={() => onThread(message)}>Open conversation</Button> : null}</div>
  </div> }];
  return <ControlBandProvider host={undefined}><ResourceViewProvider scope="local" initialState={{ pageSize: 10, sorting: [{ id: "latest", desc: true }] }}>
    <section aria-label="Related results" className={`min-w-0 [contain:inline-size] ${className}`}>
      <SurfaceHeader title={kind === "file" ? "Same file" : "Same text"} subtitle={`${rows.length} accessible messages · all accounts`}
        density="compact" headingLevel={2} actions={<Button size="sm" variant="ghost" aria-label="Close Related" onClick={onClose}><Glyph name="x" /></Button>} />
      <div className="space-y-2 border-b border-border-subtle p-3"><Tag>Pinned connection</Tag>
        <p className="break-words text-13 font-medium">{kind === "file" ? name ?? "File unavailable" : fragments[id!] ?? "Text unavailable"}</p>
        <p className="text-2xs text-fg-muted">Independent of sender, coverage and conversation filters. Selecting a use keeps this connection open.</p>
      </div>
      <PageToolbar density="compact" start={<CollectionOrder label="Related order" />} />
      <RowsListView rows={rows} query={sampleQuery} columns={columns} groupOptions={[]} filterOptions={[]}
        activeRowId={selected} emptyContent="No accessible matching uses." />
    </section>
  </ResourceViewProvider></ControlBandProvider>;
}
