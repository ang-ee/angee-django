import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { ContextMenu, Glyph } from "@angee/ui";

const meta = {
  title: "Primitives/ContextMenu",
  component: ContextMenu,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof ContextMenu>;

export default meta;

type Story = StoryObj<typeof meta>;

export const RightClickMenu: Story = {
  render: () => <ContextMenuDemo />,
};

function ContextMenuDemo() {
  const [pinned, setPinned] = React.useState(false);
  const [view, setView] = React.useState("list");

  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger className="flex h-52 w-80 items-center justify-center rounded-8 border border-dashed border-border-subtle bg-sheet p-6 text-center text-13 text-fg-muted">
        Right-click this workspace card.
      </ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Positioner sideOffset={4}>
          <ContextMenu.Content>
            <ContextMenu.Item>
              <Glyph name="file" />
              Open record
              <ContextMenu.Shortcut>↵</ContextMenu.Shortcut>
            </ContextMenu.Item>
            <ContextMenu.Item>
              <Glyph name="star" />
              Favorite
            </ContextMenu.Item>
            <ContextMenu.CheckboxItem
              checked={pinned}
              onCheckedChange={setPinned}
            >
              <ContextMenu.CheckboxItemIndicator />
              Pin in sidebar
            </ContextMenu.CheckboxItem>
            <ContextMenu.Separator />
            <ContextMenu.Group>
              <ContextMenu.Label>View as</ContextMenu.Label>
              <ContextMenu.RadioGroup
                value={view}
                onValueChange={(value) => setView(String(value))}
              >
                <ContextMenu.RadioItem value="list">
                  <ContextMenu.RadioItemIndicator />
                  List
                </ContextMenu.RadioItem>
                <ContextMenu.RadioItem value="activity">
                  <ContextMenu.RadioItemIndicator />
                  Activity
                </ContextMenu.RadioItem>
              </ContextMenu.RadioGroup>
            </ContextMenu.Group>
            <ContextMenu.Separator />
            <ContextMenu.SubmenuRoot>
              <ContextMenu.SubmenuTrigger>Move to</ContextMenu.SubmenuTrigger>
              <ContextMenu.Portal>
                <ContextMenu.Positioner
                  side="right"
                  align="start"
                  sideOffset={6}
                >
                  <ContextMenu.Content>
                    <ContextMenu.Item>Inbox</ContextMenu.Item>
                    <ContextMenu.Item>Current sprint</ContextMenu.Item>
                    <ContextMenu.Item>Archive</ContextMenu.Item>
                  </ContextMenu.Content>
                </ContextMenu.Positioner>
              </ContextMenu.Portal>
            </ContextMenu.SubmenuRoot>
            <ContextMenu.Item variant="danger">
              <Glyph name="archive" />
              Archive record
            </ContextMenu.Item>
          </ContextMenu.Content>
        </ContextMenu.Positioner>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}
