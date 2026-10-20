import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, CountBadge, Glyph, Tabs } from "@angee/ui";

/**
 * The one numeric count pill: inline beside a label (a tab, a scope chip, a
 * tree row) or overlaid on an icon button's corner. Counts format through
 * `formatNumber`.
 */
const meta = {
  title: "Primitives/CountBadge",
  component: CountBadge,
  parameters: { layout: "centered" },
  args: { children: 12 },
} satisfies Meta<typeof CountBadge>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

/** Sizes and tones, inline. */
export const SizesAndTones: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <CountBadge size="xs">3</CountBadge>
      <CountBadge size="sm">12</CountBadge>
      <CountBadge size="md">1240</CountBadge>
      <CountBadge tone="muted">4</CountBadge>
      <CountBadge tone="warning">2</CountBadge>
      <CountBadge tone="danger" variant="solid">9</CountBadge>
    </div>
  ),
};

/** Overlaid on an icon button's corner through the button's `badge` slot, and standalone. */
export const Overlay: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      <Button size="iconSm" variant="ghost" aria-label="Needs you · 2" badge={2}><Glyph name="bell" /></Button>
      <span className="relative inline-flex size-8 items-center justify-center rounded-6 bg-inset">
        <Glyph name="inbox" />
        <CountBadge size="xs" placement="overlay" tone="danger" variant="solid">7</CountBadge>
      </span>
    </div>
  ),
};

/** Tab counts compose it. */
export const InTabs: Story = {
  render: () => (
    <Tabs defaultValue="timeline" variant="card" className="w-80">
      <Tabs.List>
        <Tabs.Tab value="timeline">Timeline<Tabs.Count>5</Tabs.Count></Tabs.Tab>
        <Tabs.Tab value="discussion">Discussion<Tabs.Count>2</Tabs.Count></Tabs.Tab>
        <Tabs.Tab value="tasks">Tasks</Tabs.Tab>
      </Tabs.List>
    </Tabs>
  ),
};
