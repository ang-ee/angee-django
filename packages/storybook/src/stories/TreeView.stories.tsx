import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { InlineTextAction, PaneToolbar, PaneToolbarHost, TreeView } from "@angee/ui";

/**
 * The hierarchical view: flat rows with a parent pointer folded into a `Tree`.
 * A new item is typed as a row in the tree under its parent (`insertion`),
 * where the user is looking, never in a field at the pane's foot.
 */
const meta = {
  title: "Views/TreeView",
  component: TreeView,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof TreeView>;

export default meta;

type Story = StoryObj;

interface Row extends Record<string, unknown> {
  id: string;
  parent: string | null;
  title: string;
  icon: string;
  count?: number;
}

const initialRows: readonly Row[] = [
  { id: "policies", parent: null, title: "Policies", icon: "folder", count: 3 },
  { id: "contracts", parent: null, title: "Supplier contracts", icon: "folder", count: 2 },
  { id: "cloudflare", parent: "contracts", title: "Cloudflare, Inc. agreement", icon: "file-text" },
  { id: "hetzner", parent: "contracts", title: "Hetzner agreement", icon: "file-text" },
  { id: "runbooks", parent: null, title: "Runbooks", icon: "folder", count: 1 },
  { id: "close", parent: "runbooks", title: "Month-end close", icon: "file-text" },
];

function Navigator({ creating: initialCreating = false }: { creating?: boolean }) {
  const [rows, setRows] = useState(initialRows);
  const [selected, setSelected] = useState<string>("cloudflare");
  const [creating, setCreating] = useState(initialCreating);
  const parent = rows.find((row) => row.id === selected);
  const insertParent = parent ? (parent.icon === "folder" ? parent.id : parent.parent) : null;
  return (
    <div className="h-[420px] w-72 border-r border-border-subtle bg-canvas">
      <PaneToolbarHost>
        <PaneToolbar actions={[{ icon: "plus", label: "New", text: true, items: [
          { label: "Page", icon: "file-text", onClick: () => setCreating(true) },
          { label: "Folder", icon: "folder", onClick: () => setCreating(true) },
        ] }]} />
        <TreeView<Row>
          rows={rows}
          parent="parent"
          rowKey="id"
          label="title"
          icon="icon"
          badge="count"
          selectedId={selected}
          onSelect={(row) => setSelected(row.id)}
          insertion={creating ? {
            parentId: insertParent,
            node: (
              <InlineTextAction
                open
                onOpenChange={(next) => { if (!next) setCreating(false); }}
                onSubmit={(title) => {
                  const id = `new-${rows.length}`;
                  setRows((current) => [...current, { id, parent: insertParent, title, icon: "file-text" }]);
                  setSelected(id);
                  setCreating(false);
                }}
                inputLabel="Page title"
                placeholder="Page title"
                submitLabel="Create"
                formClassName="w-full items-center gap-1"
                inputClassName="min-w-0 flex-1"
              />
            ),
          } : null}
          className="min-h-0 flex-1 overflow-auto"
        />
      </PaneToolbarHost>
    </div>
  );
}

/** Folders with counts and pages, one selected. */
export const Default: Story = { render: () => <Navigator /> };

/** Creating: the new page is typed as a row under the selected folder; Enter creates and selects it, Escape cancels. */
export const Insertion: Story = { render: () => <Navigator creating /> };
