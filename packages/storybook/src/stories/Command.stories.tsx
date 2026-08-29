import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Command, Glyph, Kbd } from "@angee/ui";

const meta = {
  title: "Primitives/Command",
  component: Command,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof Command>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Palette: Story = {
  render: () => <CommandPaletteDemo />,
};

function CommandPaletteDemo() {
  const [selected, setSelected] = React.useState("No command selected");

  return (
    <div className="w-[30rem] overflow-hidden rounded-8 border border-border-subtle bg-popover shadow-popover">
      <Command label="Command palette" loop>
        <Command.Search>
          <Command.Input placeholder="Search commands..." />
          <Kbd size="sm">⌘K</Kbd>
        </Command.Search>
        <Command.List>
          <Command.Empty>No matching commands.</Command.Empty>
          <Command.Group heading="Records">
            <Command.Item
              value="open notes"
              keywords={["record", "notes"]}
              onSelect={setSelected}
            >
              <Glyph name="file" />
              Open notes
              <Command.Shortcut>⌘O</Command.Shortcut>
            </Command.Item>
            <Command.Item
              value="show lists"
              keywords={["view", "table"]}
              onSelect={setSelected}
            >
              <Glyph name="list" />
              Show lists
            </Command.Item>
            <Command.Item
              value="favorite current record"
              onSelect={setSelected}
            >
              <Glyph name="star" />
              Favorite current record
            </Command.Item>
          </Command.Group>
          <Command.Separator />
          <Command.Group heading="System">
            <Command.Item value="global search" onSelect={setSelected}>
              <Glyph name="search" />
              Global search
              <Command.Shortcut>/</Command.Shortcut>
            </Command.Item>
            <Command.Item value="activity feed" onSelect={setSelected}>
              <Glyph name="activity" />
              Activity feed
            </Command.Item>
            <Command.Item disabled value="sync resources">
              <Glyph name="archive" />
              Sync resources
            </Command.Item>
          </Command.Group>
        </Command.List>
      </Command>
      <div className="border-t border-border-subtle px-3 py-2 text-xs text-fg-muted">
        {selected}
      </div>
    </div>
  );
}
