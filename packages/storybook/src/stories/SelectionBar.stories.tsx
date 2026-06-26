import type { Meta, StoryObj } from "@storybook/react-vite";
import { Glyph, SelectionBar } from "@angee/ui";

const meta = {
  title: "Primitives/SelectionBar",
  component: SelectionBar,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    position: {
      control: "select",
      options: ["static", "sticky"],
    },
    surface: {
      control: "select",
      options: ["brand", "sheet"],
    },
  },
  args: {
    count: 12,
    position: "static",
    surface: "brand",
    summary: "Notes in Ready for review",
  },
} satisfies Meta<typeof SelectionBar>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: ({ count, position, summary, surface }) => (
    <SelectionBar
      count={count}
      position={position}
      surface={surface}
      summary={summary}
      actions={
        <>
          <SelectionBar.Action surface={surface}>
            <Glyph name="archive" />
            Archive
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface}>
            <Glyph name="star" />
            Favorite
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface} tone="danger">
            <Glyph name="x" />
            Clear
          </SelectionBar.Action>
        </>
      }
    />
  ),
};

export const SheetSurface: Story = {
  args: {
    count: 4,
    surface: "sheet",
    summary: "Invoices selected",
  },
  render: ({ count, position, summary, surface }) => (
    <SelectionBar
      count={count}
      position={position}
      surface={surface}
      summary={summary}
      onClear={() => undefined}
    />
  ),
};
