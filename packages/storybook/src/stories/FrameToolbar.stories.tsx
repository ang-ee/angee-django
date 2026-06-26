import type { Meta, StoryObj } from "@storybook/react-vite";
import { FrameToolbar, Glyph, Toolbar } from "@angee/ui";

const meta = {
  title: "Fragments/FrameToolbar",
  component: FrameToolbar,
  parameters: { layout: "padded" },
} satisfies Meta<typeof FrameToolbar>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="overflow-hidden rounded-md border border-border-subtle">
      <FrameToolbar
        end={
          <>
            <Toolbar.Button aria-label="Previous" buttonSize="iconSm">
              <Glyph name="chevron-left" />
            </Toolbar.Button>
            <Toolbar.Button aria-label="Next" buttonSize="iconSm">
              <Glyph name="chevron-right" />
            </Toolbar.Button>
          </>
        }
        start={
          <>
            <Toolbar.Button>
              <Glyph name="list" />
              Outline
            </Toolbar.Button>
            <Toolbar.Button>
              <Glyph name="activity" />
              Inspect
            </Toolbar.Button>
          </>
        }
      />
      <div className="grid h-44 place-content-center bg-canvas text-13 text-fg-muted">
        Frame content
      </div>
    </div>
  ),
};

export const Freeform: Story = {
  render: () => (
    <FrameToolbar>
      <Toolbar.Group>
        <Toolbar.Button buttonTone="primary">
          <Glyph name="check" />
          Publish
        </Toolbar.Button>
      </Toolbar.Group>
      <Toolbar.Spacer />
      <Toolbar.Input aria-label="Frame name" defaultValue="Overview" />
    </FrameToolbar>
  ),
};

