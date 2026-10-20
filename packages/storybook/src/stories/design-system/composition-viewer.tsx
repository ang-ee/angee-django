import { useMemo, useState } from "react";
import { Badge, Card, CardContent, Chip, EmptyState, PaneLabel, PaneRow, PaneRows, SearchInput, SectionHeading, ToggleGroup, Tree, textRoleVariants, type TreeNode } from "@angee/ui";
import { parseCompositionReport, type CompositionReport } from "@angee/ui/catalog";
import snapshot from "../../../reports/composition.json";
import { SheetPage } from "./token-sheets";
import { storybookValueLookup } from "../../url-values";

type Node = CompositionReport["nodes"][number];
const graph = parseCompositionReport(snapshot);
const nodes = new Map(graph.nodes.filter(n => n.shipped).map(n => [n.id, n]));
// Native args mapping keeps canonical ids (including colons) out of URL values.
export const compositionRootArguments = Object.fromEntries(storybookValueLookup([...nodes.keys()]));
const argumentsByRoot = new Map(Object.entries(compositionRootArguments).map(([value, id]) => [id, value]));
export function compositionRootArgument(root: string): string {
  const value = argumentsByRoot.get(root);
  if (!value) throw new Error(`Unknown composition root: ${root}`);
  return value;
}
const edges = graph.edges.filter(e => nodes.has(e.from) && nodes.has(e.to));
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
const owners = Object.entries(graph.fan_in).filter(([id]) => nodes.has(id))
  .sort(([a, countA], [b, countB]) => countB - countA || compare(a, b));
const flagged = [...nodes.values()].filter(n => n.ownershipViolation).sort((a,b) => compare(a.id,b.id));
const noDirectOwner = graph.hand_rolled.filter(id => nodes.has(id) && !nodes.get(id)?.routeInstance);
const routeInstances = [...nodes.values()].filter(node => node.routeInstance).sort((a, b) => compare(a.id, b.id));
const duplicates = Object.entries(graph.duplicates).map(([name, ids]) => [name, ids.filter(id => nodes.has(id))] as const)
  .filter(([,ids]) => ids.length > 1).sort(([a],[b]) => compare(a,b));
const outgoing = new Map<string, { id: string; file: string | null }[]>();
const incoming = new Map<string, { id: string; file: string | null }[]>();
for (const edge of edges) {
  const down = outgoing.get(edge.from) ?? [];
  down.push({ id: edge.to, file: edge.file }); outgoing.set(edge.from, down);
  const up = incoming.get(edge.to) ?? [];
  up.push({ id: edge.from, file: edge.file }); incoming.set(edge.to, up);
}
for (const map of [incoming, outgoing]) for (const [id, links] of map) {
  map.set(id, [...new Map(links.map(link => [link.id, link])).values()].sort((a,b) => compare(a.id,b.id)));
}

function NodeLabel({ node }: { node: Node }) {
  return <span className="inline-flex min-w-0 items-center gap-2">
    <span className="truncate">{node.id}</span>
    <Chip tone={node.ownershipViolation ? "warning" : "neutral"}>{node.routeInstance ? "route instance" : node.kind ?? "infrastructure"}</Chip>
    {node.ownershipViolation && <Badge tone="danger">owner mismatch</Badge>}
  </span>;
}

export function CompositionViewer({ root, onRootChange }: { root: string; onRootChange?: (root: string) => void }) {
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState<"down" | "up">("down");
  const [list, setList] = useState<"pages" | "owners" | "flagged">("pages");
  const selection = nodes.get(root);
  const matches = [...nodes.values()].filter(n => n.id.toLowerCase().includes(search.toLowerCase()));
  const candidates = search ? matches.map(n => n.id) : list === "pages" ? graph.pages.filter(id => nodes.has(id))
    : list === "owners" ? owners.map(([id]) => id) : flagged.map(n => n.id);
  const { tree, targets } = useMemo(() => {
    const adjacent = mode === "down" ? outgoing : incoming;
    const seen = new Set<string>();
    const targets = new Map<string, string>();
    function branch(id: string, trail: string, depth: number, file?: string | null): TreeNode {
      const node = nodes.get(id);
      if (!node) throw new Error(`Unknown composition node: ${id}`);
      const links = adjacent.get(id) ?? [];
      const repeated = seen.has(id);
      seen.add(id); targets.set(trail,id);
      return {
        id: trail,
        label: <span title={file ?? node.reason}><NodeLabel node={node} />{repeated && links.length ? " · shown above" : ""}{depth === 8 && links.length ? " · depth limit" : ""}</span>,
        icon: node.kind === "token" ? "contrast" : "grid",
        iconTone: node.ownershipViolation ? "warning" : "neutral",
        count: links.length || undefined,
        defaultCollapsed: depth > 1,
        children: repeated || depth >= 8 ? undefined : links.map((link,index) => branch(link.id,`${trail}/${index}`,depth+1,link.file)),
      };
    }
    return { tree: nodes.has(root) ? [branch(root,"root",0)] : [], targets };
  }, [mode, root]);
  return <SheetPage title="Composition" description={`${nodes.size} shipped nodes · ${edges.length} edges · inventory v0 · studies excluded`}>
    <div className="grid items-start gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
      <Card><CardContent className="grid gap-4 p-4">
        <SearchInput aria-label="Find a component or token" placeholder="Find a component or token…" value={search} onChange={e => setSearch(e.target.value)} />
        <ToggleGroup multiple={false} value={[list]} aria-label="Composition roots"
          onValueChange={([value]) => { if (value === "flagged" || value === "owners" || value === "pages") setList(value); }}>
          {(["flagged", "owners", "pages"] as const).map(value => <ToggleGroup.Item key={value} value={value}>{value}</ToggleGroup.Item>)}
        </ToggleGroup>
        <SectionHeading label={search ? "Matches" : list} count={candidates.length} />
        <div className="max-h-[65vh] overflow-auto">
          <Tree aria-label="Composition roots" selectedId={root} onSelect={onRootChange}
            nodes={candidates.slice(0,300).map(id => ({ id, label: id, count: graph.fan_in[id] || undefined }))} />
        </div>
        {candidates.length > 300 && <Chip>First 300 matches · narrow your search</Chip>}
      </CardContent></Card>
      <Card><CardContent className="grid min-w-0 gap-4 p-4">
        <ToggleGroup multiple={false} value={[mode]} aria-label="Composition direction"
          onValueChange={([value]) => { if (value === "down" || value === "up") setMode(value); }}>
          <ToggleGroup.Item value="down">Composes</ToggleGroup.Item>
          <ToggleGroup.Item value="up">Composed by</ToggleGroup.Item>
        </ToggleGroup>
        {selection && <><SectionHeading label={selection.id} /><p className={textRoleVariants({ role: "caption" })}>
          {selection.routeInstance ? "Route instance · composes no page shape yet" : `Kind: ${selection.kind ?? "outside UX ontology"}`} · {selection.kindBasis}. Select a tree row to inspect it as the root. Hover for its source file.
        </p></>}
        <div className="overflow-auto"><Tree key={`${root}-${mode}`} aria-label="Composition tree" nodes={tree}
          onSelect={id => { const target = targets.get(id); if (target) onRootChange?.(target); }} /></div>
        {!tree.length && <EmptyState title="Choose an owner" description="Search for a page, component or token." />}
      </CardContent></Card>
    </div>
  </SheetPage>;
}

export function ReuseReport() {
  const [search,setSearch] = useState("");
  const match = (value: string) => value.toLowerCase().includes(search.toLowerCase());
  return <SheetPage title="Reuse" description="Fan-in counts distinct consumers outside the framework, excluding historical studies. Inferred kinds and no-direct-owner findings are review candidates, not proof of duplication.">
    <SearchInput aria-label="Filter reuse report" placeholder="Filter owners and findings…" value={search} onChange={e=>setSearch(e.target.value)} />
    <div className="grid items-start gap-4 xl:grid-cols-3">
      <Card><CardContent className="p-4"><SectionHeading label="Owners by fan-in" count={owners.length} />
        <PaneRows>{owners.filter(([id])=>match(id)).map(([id,count]) => <li key={id}><PaneRow title={id} meta={count} icon="grid"
          href={`/?path=/story/reports-composition--default&args=root:${compositionRootArgument(id)}`} target="_top"
          sub={nodes.get(id)?.kind ?? "infrastructure"} /></li>)}</PaneRows>
      </CardContent></Card>
      <Card><CardContent className="p-4"><SectionHeading label="Flagged components" count={flagged.length} />
        <PaneRows>{flagged.filter(n=>match(n.id)).map(node => <li key={node.id}><PaneRow title={node.id} tone="warning"
          detail={node.kind} sub={`UI-owned kind in addon · ${node.kindBasis}`} /></li>)}
          <PaneLabel count={noDirectOwner.length}>No direct owner imports</PaneLabel>
          {noDirectOwner.filter(match).map(id=><li key={id}><PaneRow title={id} sub="Static-import candidate; container and registry reach may be missing." /></li>)}
        </PaneRows>
      </CardContent></Card>
      <Card><CardContent className="p-4"><SectionHeading label="Duplicate names" count={duplicates.length} />
        <PaneRows>{duplicates.filter(([name,ids])=>match(name)||ids.some(match)).map(([name,ids])=>
          <li key={name}><PaneRow title={name} meta={ids.length} sub={ids.join(" · ")} /></li>)}</PaneRows>
      </CardContent></Card>
    </div>
    <Card><CardContent className="p-4">
      <SectionHeading label="Composes no page shape yet" count={routeInstances.length} />
      <p className={textRoleVariants({ role: "description" })}>Addon route instances will compose the shared page shapes introduced in lane 2. These are information, not ownership violations.</p>
      <PaneRows>{routeInstances.filter(node => match(node.id)).map(node => <li key={node.id}>
        <PaneRow title={node.id} sub="Route instance · page shape pending" />
      </li>)}</PaneRows>
    </CardContent></Card>
  </SheetPage>;
}
