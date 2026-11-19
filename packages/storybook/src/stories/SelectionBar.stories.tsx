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
            <Glyph name="star" />
            Favorite
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface}>
            <Glyph name="archive" />
            Archive
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface}>
            <Glyph name="trash" />
            Move to trash
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface} tone="danger">
            <Glyph name="trash" />
            Delete
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface}>
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
    summary: "Documents selected",
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

/** A verb that cannot run for this selection says why in its tooltip and stays focusable. */
export const WithDisabledVerb: Story = {
  args: {
    count: 3,
    summary: "People selected",
  },
  render: ({ count, position, summary, surface }) => (
    <SelectionBar
      count={count}
      position={position}
      surface={surface}
      summary={summary}
      actions={
        <>
          <SelectionBar.Action surface={surface} disabledReason="Select two people to compare them.">
            <Glyph name="users" />
            Merge…
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface}>
            <Glyph name="archive" />
            Archive
          </SelectionBar.Action>
        </>
      }
    />
  ),
};

/** Every loaded row is ticked and the view holds more: the bar offers all of them. */
export const WithAllMatchingOffered: Story = {
  args: {
    count: 20,
    summary: undefined,
  },
  render: ({ count, position, surface }) => (
    <SelectionBar
      count={count}
      countLabel={`${count} selected`}
      position={position}
      surface={surface}
      actions={
        <>
          <SelectionBar.Action surface={surface}>Select all 120 records</SelectionBar.Action>
          <SelectionBar.Action surface={surface}>
            <Glyph name="archive" />
            Archive
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface}>Clear</SelectionBar.Action>
        </>
      }
    />
  ),
};

/** Every record the view matches is selected: a verb that acts on each ticked row says why it cannot. */
export const WithAllMatchingSelected: Story = {
  args: {
    count: 20,
    summary: undefined,
  },
  render: ({ position, surface }) => (
    <SelectionBar
      count={120}
      countLabel="All 120 records selected"
      position={position}
      surface={surface}
      actions={
        <>
          <SelectionBar.Action surface={surface}>
            <Glyph name="users" />
            Merge…
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface} disabledReason="This acts on the rows you tick, not on all 120 records">
            <Glyph name="archive" />
            Archive
          </SelectionBar.Action>
          <SelectionBar.Action surface={surface}>Clear</SelectionBar.Action>
        </>
      }
    />
  ),
};
