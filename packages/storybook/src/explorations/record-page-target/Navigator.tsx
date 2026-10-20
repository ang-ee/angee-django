import { useState } from "react";
import {
  EmptyState, Input, PaneLabel, PaneRow, PaneRows, PaneToolbar, PaneToolbarHost,
  PaneToolbarMenu, ScopedExplorerPane, ScopeChip, StatusDot, TreeView,
  statusTone, textRoleVariants, usePrimaryPaneContent,
} from "@angee/ui";
import { bill, billRows, lensOptions, lensRows, navigatorCounts, senders, trees, type InboxLens, type PageKind, type TreeRow } from "./fixtures";
import { ChoiceMenu, Spec } from "./spec";

export function Navigator({ page }: { page: PageKind }) {
  return <nav aria-label="Navigator" className="target-navigator" data-region="navigator">
    {page === "file" || page === "knowledge" ? <PublishedTree /> : <ListNavigator page={page} />}
  </nav>;
}

function PublishedTree() {
  const { node } = usePrimaryPaneContent();
  return <>{node}</>;
}

export function TreeNavigator({ page }: { page: "file" | "knowledge" }) {
  const [search, setSearch] = useState(false);
  const [query, setQuery] = useState("");
  const rootName = page === "file" ? "AP drive" : "Accounts Payable";
  return <ScopedExplorerPane<{ id: string; name: string }, TreeRow>
    roots={[{ id: page, name: rootName }]}
    getRootId={(root) => root.id} getRootLabel={(root) => root.name}
    getTreeRows={() => trees[page]} defaultSelectedId={page}
    navigatorLabel={page === "file" ? "Files in AP drive" : "Pages in Accounts Payable"}
    rootPicker={{ "aria-label": page === "file" ? "Drive" : "Vault" }}
    toolbar={() => ({
      search: true, onSearch: () => setSearch(!search),
      actions: [{ icon: "plus", label: page === "file" ? "New folder" : "New page" }],
      overflow: <PaneToolbarMenu label="Navigator options" items={[{ label: "Clear search", icon: "search", onClick: () => setQuery("") }]} />,
    })}
    renderTree={(controller) => <>
      {search ? <Input aria-label="Search navigator" placeholder="Search…" value={query} onChange={(event) => setQuery(event.target.value)} /> : null}
      {query ? <PaneRows className="px-2 py-2"><PaneLabel>Search results</PaneLabel>
        {controller.treeRows.filter((row) => row.title.toLowerCase().includes(query.toLowerCase())).map((row) => <li key={row.id}><PaneRow title={row.title} icon={row.icon} selected={row.id === page} /></li>)}
        {!controller.treeRows.some((row) => row.title.toLowerCase().includes(query.toLowerCase())) ? <li><EmptyState title="No matches" icon="search" className="min-h-36 p-4" /></li> : null}
      </PaneRows> : <TreeView rows={controller.treeRows}
        parent="parent" rowKey="id" label="title" icon="icon"
        selectedId={controller.selectedId}
        renderRow={(row) => <PaneRow title={row.title} className="py-0.5" leading={<span />} />}
        className="min-h-0 flex-1 overflow-auto" />}
    </>}
  >{() => null}</ScopedExplorerPane>;
}

function ListNavigator({ page }: { page: "bill" | "nexus" }) {
  const [search, setSearch] = useState(false);
  const [query, setQuery] = useState("");
  const [view, setView] = useState("review");
  const [lens, setLens] = useState<InboxLens>("Senders");
  const [scope, setScope] = useState("all");
  const selected = page === "bill" ? bill.number : senders[0]!.name;
  const nexus = page === "nexus";
  const rows = billRows.filter((row) => `${row.label} ${row.supplier}`.toLowerCase().includes(query.toLowerCase()));
  const people = lensRows[lens].filter((row) => `${row.name} ${row.address}`.toLowerCase().includes(query.toLowerCase()));
  return <>
    <Spec owner={nexus ? "Navigator · lens wiring" : "Navigator · list / views"} className="shrink-0">
    <PaneToolbarHost className="h-auto flex-none">
    <PaneToolbar selector={
      <ChoiceMenu label={nexus ? "Navigator lens" : "Vendor bill view"} value={nexus ? lens : view}
        onChange={nexus ? (value) => { if (value === "Senders" || value === "Recipients" || value === "Circles") setLens(value); } : setView}
        items={nexus ? lensOptions
          : [{ value: "review", label: "Vendor bills · To review" }, { value: "all", label: "Vendor bills · All bills" }]}>
        <span className="truncate">{nexus ? lens : "Vendor bills"}</span>
      </ChoiceMenu>
    }
      search onSearch={() => setSearch(!search)}
      actions={[{ icon: "plus", label: nexus ? "New message" : "New bill" }]}
      overflow={<PaneToolbarMenu label="Navigator options" groups={[{
        label: "Order", value: "recent", onValueChange: () => undefined,
        items: [{ value: "recent", label: "Latest activity" }],
      }]} />} />
    <div className="flex gap-1 border-b border-border-subtle px-2 py-2">
      <ScopeChip label={nexus ? "Everyone" : view === "review" ? "To review" : "All bills"} count={nexus ? navigatorCounts.senders : navigatorCounts.bills} active={scope === "all"} onClick={() => setScope("all")} />
      <ScopeChip label={nexus ? "Unread" : "Needs you"} count={nexus ? navigatorCounts.unread : 1} active={scope === "needs"} onClick={() => setScope("needs")} />
    </div>
    </PaneToolbarHost>
    </Spec>
    {search ? <div className="px-2 py-2"><Input aria-label="Search navigator" placeholder={nexus ? "Find a sender…" : "Find a bill…"} value={query} onChange={(event) => setQuery(event.target.value)} /></div> : null}
    <PaneRows className="min-h-0 flex-1 overflow-auto px-2 py-2">
      <PaneLabel>{nexus ? `${lens} · Latest activity` : `Vendor bills · ${view === "review" ? "To review" : "All bills"}`}</PaneLabel>
      {nexus ? people.filter((sender) => scope !== "needs" || sender.name === selected).map((sender) => <li key={sender.name}><PaneRow
          icon={lens === "Circles" ? "users" : "user"} title={sender.name} sub={sender.address} meta={sender.count}
          selected={selected === sender.name} /></li>)
        : rows.filter((row) => scope !== "needs" || row.id === bill.number).map((row) => <li key={row.id}><PaneRow
          leading={<StatusDot tone={statusTone(row.status)} size="sm" className="mt-1" />}
          title={row.label} sub={row.supplier} meta={row.amount}
          selected={selected === row.id} className={selected === row.id ? "bg-canvas" : undefined} /></li>)}
      {query && (nexus ? people : rows).length === 0 ? <li><EmptyState title="No matches" icon="search" className="min-h-36 p-4" /></li> : null}
    </PaneRows>
    <p className={`${textRoleVariants({ role: "caption" })} border-t border-border-subtle px-3 py-3`}>
      {nexus ? "1,462 senders · 4,010 matching messages" : `${navigatorCounts.bills} bills · ${bill.company}`}
    </p>
  </>;
}
