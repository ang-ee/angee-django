import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  AppRuntimeProvider,
  EmptyState,
  PrimaryPaneProvider,
  ScopedExplorerPane,
  TreeView,
  baseIcons,
  defaultWidgets,
  usePrimaryPaneContent,
} from "@angee/ui";

interface Root {
  id: string;
  name: string;
}

type TreeRow = {
  id: string;
  parent: string | null;
  title: string;
  icon: string;
};

const roots: readonly Root[] = [
  { id: "docs", name: "Docs" },
  { id: "media", name: "Media" },
];

const rowsByRoot: Record<string, readonly TreeRow[]> = {
  docs: [
    { id: "handbook", parent: null, title: "Handbook", icon: "book-open" },
    { id: "api", parent: "handbook", title: "API notes", icon: "file-text" },
  ],
  media: [
    { id: "logos", parent: null, title: "Logos", icon: "folder" },
    { id: "screens", parent: null, title: "Screenshots", icon: "image" },
  ],
};

const meta = {
  title: "Views/ScopedExplorerPane",
  component: ScopedExplorerPane,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof ScopedExplorerPane>;

export default meta;

type Story = StoryObj;

function PrimaryPaneHost(): React.ReactElement {
  const { node } = usePrimaryPaneContent();
  return (
    <aside className="h-[420px] w-72 border-r border-border-subtle bg-canvas">
      {node}
    </aside>
  );
}

function ScopedExplorerDemo(): React.ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets }}>
      <PrimaryPaneProvider>
        <div className="flex min-h-[420px] bg-sheet">
          <PrimaryPaneHost />
          <main className="grid flex-1 place-content-center p-8">
            <ScopedExplorerPane
              roots={roots}
              getRootId={(root) => root.id}
              getRootLabel={(root) => root.name}
              getTreeRows={(rootId) => rowsByRoot[rootId] ?? []}
              navigatorLabel="Library"
              rootPicker={{ "aria-label": "Library root" }}
              renderTree={(controller) => (
                <TreeView<TreeRow>
                  rows={controller.treeRows}
                  parent="parent"
                  rowKey="id"
                  label="title"
                  icon="icon"
                  selectedId={controller.selectedId}
                  onSelect={(row) => controller.setSelectedId(row.id)}
                  className="min-h-0 flex-1 overflow-auto"
                />
              )}
              emptyContent={
                <EmptyState
                  fill
                  icon="folder"
                  title="No libraries"
                  description="Add a root library to begin."
                />
              }
            >
              {(controller) => (
                <div className="text-sm text-fg">
                  Selected root: {controller.root?.name ?? "None"}
                </div>
              )}
            </ScopedExplorerPane>
          </main>
        </div>
      </PrimaryPaneProvider>
    </AppRuntimeProvider>
  );
}

export const Pane: Story = {
  render: () => <ScopedExplorerDemo />,
};
