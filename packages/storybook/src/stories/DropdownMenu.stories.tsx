import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, DropdownMenu, Glyph } from "@angee/ui";

const meta = {
  title: "Primitives/DropdownMenu",
  component: DropdownMenu,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof DropdownMenu>;

export default meta;

type Story = StoryObj<typeof meta>;

export const FullMenu: Story = {
  render: () => <DropdownMenuDemo />,
};

function DropdownMenuDemo() {
  const [toolbar, setToolbar] = React.useState(true);
  const [density, setDensity] = React.useState("comfortable");

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        render={
          <Button variant="secondary">
            Actions
            <Glyph name="chevron-down" />
          </Button>
        }
      />
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner sideOffset={6} align="start">
          <DropdownMenu.Content>
            <DropdownMenu.Group>
              <DropdownMenu.Label>Document</DropdownMenu.Label>
              <DropdownMenu.Item>
                <Glyph name="file" />
                Open
                <DropdownMenu.Shortcut>⌘O</DropdownMenu.Shortcut>
              </DropdownMenu.Item>
              <DropdownMenu.Item>
                <Glyph name="star" />
                Add to favorites
              </DropdownMenu.Item>
              <DropdownMenu.CheckboxItem
                checked={toolbar}
                onCheckedChange={setToolbar}
              >
                <DropdownMenu.CheckboxItemIndicator />
                Show toolbar
              </DropdownMenu.CheckboxItem>
            </DropdownMenu.Group>
            <DropdownMenu.Separator />
            <DropdownMenu.Group>
              <DropdownMenu.Label>Density</DropdownMenu.Label>
              <DropdownMenu.RadioGroup
                value={density}
                onValueChange={(value) => setDensity(String(value))}
              >
                <DropdownMenu.RadioItem value="compact">
                  <DropdownMenu.RadioItemIndicator />
                  Compact
                </DropdownMenu.RadioItem>
                <DropdownMenu.RadioItem value="comfortable">
                  <DropdownMenu.RadioItemIndicator />
                  Comfortable
                </DropdownMenu.RadioItem>
              </DropdownMenu.RadioGroup>
            </DropdownMenu.Group>
            <DropdownMenu.Separator />
            <DropdownMenu.SubmenuRoot>
              <DropdownMenu.SubmenuTrigger>Export</DropdownMenu.SubmenuTrigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Positioner
                  side="right"
                  align="start"
                  sideOffset={6}
                >
                  <DropdownMenu.Content>
                    <DropdownMenu.Item>CSV</DropdownMenu.Item>
                    <DropdownMenu.Item>JSON</DropdownMenu.Item>
                    <DropdownMenu.Item>PDF</DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Positioner>
              </DropdownMenu.Portal>
            </DropdownMenu.SubmenuRoot>
            <DropdownMenu.Item variant="danger">
              <Glyph name="archive" />
              Archive
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
