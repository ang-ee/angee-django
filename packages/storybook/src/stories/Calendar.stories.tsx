import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Calendar } from "@angee/ui";

const meta = {
  title: "Primitives/Calendar",
  component: Calendar,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    size: {
      control: "select",
      options: ["sm", "md"],
    },
  },
  args: {
    fixedWeeks: true,
    month: new Date(2026, 5, 1),
    showOutsideDays: true,
    size: "md",
  },
} satisfies Meta<typeof Calendar>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const SingleSelection: Story = {
  render: () => {
    const [selected, setSelected] = React.useState<Date | undefined>(
      new Date(2026, 5, 16),
    );

    return (
      <Calendar
        fixedWeeks
        mode="single"
        month={new Date(2026, 5, 1)}
        onSelect={(next) => setSelected(next)}
        selected={selected}
        showOutsideDays
      />
    );
  },
};

export const RangeSelection: Story = {
  render: () => {
    const [selected, setSelected] = React.useState<
      { from: Date | undefined; to?: Date | undefined } | undefined
    >({
      from: new Date(2026, 5, 8),
      to: new Date(2026, 5, 13),
    });

    return (
      <Calendar
        fixedWeeks
        mode="range"
        month={new Date(2026, 5, 1)}
        onSelect={(next) => setSelected(next)}
        selected={selected}
        showOutsideDays
      />
    );
  },
};

export const Compact: Story = {
  args: {
    fixedWeeks: true,
    month: new Date(2026, 5, 1),
    selected: new Date(2026, 5, 16),
    showOutsideDays: true,
    size: "sm",
  },
};
