import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, Tooltip } from "@angee/ui";

/**
 * The one tooltip: names icon-only controls and explains a disabled verb. A
 * disabled band verb stays focusable so keyboard users reach the reason.
 */
const meta = {
  title: "Primitives/Tooltip",
  component: Tooltip,
  parameters: { layout: "centered" },
} satisfies Meta<typeof Tooltip>;

export default meta;

type Story = StoryObj;

export const Default: Story = {
  render: () => (
    <div className="flex items-center gap-4 p-8">
      <Tooltip label="Follow this record"><Button size="iconSm" variant="ghost" aria-label="Follow"><Glyph name="bell" /></Button></Tooltip>
      <Tooltip label="Post the bill before registering a payment." side="bottom">
        <Button size="sm" variant="secondary" disabled focusableWhenDisabled>Register payment</Button>
      </Tooltip>
      <Tooltip label="Share" side="right"><Button size="iconSm" variant="ghost" aria-label="Share"><Glyph name="users" /></Button></Tooltip>
    </div>
  ),
};
