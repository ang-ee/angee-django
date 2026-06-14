import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Spotlight } from "@angee/base";

import { spotlightCommands } from "./chrome-fixtures";

const meta = {
  title: "Chrome/Spotlight",
  component: Spotlight,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof Spotlight>;

export default meta;

type Story = StoryObj;

export const Open: Story = {
  render: () => <SpotlightDemo />,
};

function SpotlightDemo() {
  const [open, setOpen] = React.useState(true);
  return (
    <>
      <Button type="button" onClick={() => setOpen(true)}>
        Open Spotlight
      </Button>
      <Spotlight
        open={open}
        onOpenChange={setOpen}
        commands={spotlightCommands}
      />
    </>
  );
}
