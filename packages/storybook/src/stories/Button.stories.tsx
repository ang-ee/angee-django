import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph } from "@angee/ui";

const meta = {
  title: "Primitives/Button",
  component: Button,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    variant: {
      control: "select",
      options: ["primary", "secondary", "ghost", "danger", "link"],
    },
    size: {
      control: "select",
      options: ["sm", "md", "lg", "iconSm", "iconMd", "iconLg"],
    },
  },
  args: {
    children: "Create note",
    size: "md",
    variant: "primary",
  },
} satisfies Meta<typeof Button>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {};

export const Variants: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="primary">Primary</Button>
      <Button variant="secondary">Secondary</Button>
      <Button variant="ghost">Ghost</Button>
      <Button variant="danger">Danger</Button>
      <Button variant="link">Link</Button>
      <Button variant="ghost" size="iconMd" aria-label="Search">
        <Glyph name="search" size={16} />
      </Button>
    </div>
  ),
};

/** The one icon button: a ghost `iconSm` carries every pane, band and header control. */
export const IconControls: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="ghost" size="iconSm" aria-label="Search">
        <Glyph name="search" />
      </Button>
      <Button variant="ghost" size="iconSm" aria-label="Following" pressed>
        <Glyph name="bell" />
      </Button>
      <Button variant="ghost" size="iconSm" aria-label="Needs you · 3" badge={3}>
        <Glyph name="triangle-alert" />
      </Button>
      <Button variant="ghost" size="iconSm" aria-label="Search" disabled>
        <Glyph name="search" />
      </Button>
    </div>
  ),
};

export const Loading: Story = {
  args: {
    loading: true,
    loadingText: "Saving",
    variant: "primary",
  },
};
