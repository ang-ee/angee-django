import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { GraphEditor, placeGraphNodeBeside, type GraphEditorNode, type GraphEditorLink, type GraphEditorLayout, type GraphEditorSelection, type GraphViewPosition } from "@angee/ui";

const meta = { title: "Views/GraphEditor", component: GraphEditor, parameters: { layout: "fullscreen" } } satisfies Meta<typeof GraphEditor>;
export default meta;
type Story = StoryObj;

const initialNodes: GraphEditorNode[] = [
  { id: "a", kind: "node", title: "Alpha", ariaLabel: "Alpha", ports: [{ id: "left", label: "Left" }, { id: "right", label: "Right" }] },
  { id: "b", kind: "node", title: "Beta", ariaLabel: "Beta", ports: [{ id: "next", label: "Next" }] },
  { id: "c", kind: "node", title: "Gamma", ariaLabel: "Gamma", ports: [] },
];

export const Controlled: Story = { render: () => <EditorFixture /> };
export const LeftToRight: Story = { render: () => <EditorFixture horizontal /> };
export const ReadOnly: Story = { render: () => <EditorFixture readOnly /> };

function EditorFixture({ readOnly = false, horizontal = false }: { readOnly?: boolean; horizontal?: boolean }): React.ReactElement {
  const [nodes, setNodes] = React.useState(initialNodes);
  const [links, setLinks] = React.useState<GraphEditorLink[]>([{ from: "a", port: "left", to: "b" }]);
  const [layout, setLayout] = React.useState<GraphEditorLayout>(horizontal ? {} : { a: { x: 40, y: 40 }, b: { x: 40, y: 230 }, c: { x: 310, y: 230 } });
  const [selected, setSelected] = React.useState<GraphEditorSelection>({ nodes: [], link: null });
  const nextId = React.useRef(1);
  const sameLink = (left: GraphEditorLink, right: GraphEditorLink) => left.from === right.from && left.port === right.port && left.to === right.to;
  function addNode(source: string, duplicate?: GraphEditorNode, suggested?: GraphViewPosition): string {
    const id = `node-${nextId.current++}`;
    const node = { ...(duplicate ?? { kind: "node", ports: [{ id: "next", label: "Next" }] }), id, title: id, ariaLabel: id };
    setNodes((current) => [...current, node]);
    const size = { width: 188, height: 100 };
    const point = layout[source] ?? { x: 40, y: 40 };
    const occupied = Object.values(layout).map((position) => ({ ...position, ...size }));
    setLayout((current) => ({ ...current, [id]: suggested ?? placeGraphNodeBeside({ ...point, ...size }, size, occupied, duplicate ? "right" : "below") }));
    return id;
  }
  return <GraphEditor
    className="h-screen bg-sheet" nodes={nodes} links={links} layout={layout}
    layoutOptions={horizontal ? { rankdir: "LR" } : undefined}
    selected={selected} onSelectionChange={setSelected} readOnly={readOnly}
    status={{ a: { label: "Selected", tone: "info" } }}
    canLink={(from, _port, to) => from !== to}
    onLayoutChange={setLayout}
    onLink={(link) => setLinks((current) => [...current, link])}
    onUnlink={(link) => setLinks((current) => current.filter((entry) => !sameLink(entry, link)))}
    onDelete={(ids) => {
      setNodes((current) => current.filter((node) => !ids.includes(node.id)));
      setLinks((current) => current.filter((link) => !ids.includes(link.from) && !ids.includes(link.to)));
      setLayout((current) => Object.fromEntries(Object.entries(current).filter(([id]) => !ids.includes(id))));
    }}
    onAddFromPort={(from, port, position) => {
      const to = addNode(from, undefined, position);
      setLinks((current) => [...current, { from, port, to }]);
    }}
    onInsertOnLink={(link, position) => {
      const id = addNode(link.from, undefined, position);
      setLinks((current) => [...current.filter((entry) => !sameLink(entry, link)), { ...link, to: id }, { from: id, port: "next", to: link.to }]);
    }}
    nodeActions={() => [{ id: "duplicate", label: "Duplicate", onSelect: (node) => { addNode(node.id, node); } }]}
  />;
}
