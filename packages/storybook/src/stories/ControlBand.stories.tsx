import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Button, ControlBand, ControlBandProvider, Glyph, SearchInput } from "@angee/ui";

/**
 * The page's flush control row. Under a layout it portals into the layout's
 * `area-control` host; with `host={undefined}` (a dialog, a test, a standalone
 * surface) the band renders inline where it is declared.
 */
const meta = {
  title: "Page/ControlBand",
  component: ControlBand,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ControlBand>;

export default meta;

type Story = StoryObj;

function Controls() {
  return (
    <ControlBand>
      <Button size="sm" variant="primary"><Glyph name="plus" />New</Button>
      <Button size="sm" variant="secondary">Import</Button>
      <div className="min-w-2 flex-1" />
      <SearchInput aria-label="Search records" placeholder="Search" className="w-56" />
      <Button size="iconSm" variant="ghost" aria-label="Refresh"><Glyph name="refresh" /></Button>
    </ControlBand>
  );
}

/** Inline: no host, the band is a toolbar row where it is rendered. */
export const Default: Story = {
  render: () => (
    <ControlBandProvider host={undefined}>
      <Controls />
    </ControlBandProvider>
  ),
};

/** Portaled: the band renders into the layout's host above the content that declared it. */
export const IntoHost: Story = {
  render: function IntoHost() {
    const [host, setHost] = useState<HTMLElement | null>(null);
    return (
      <div className="overflow-hidden rounded-6 border border-border-subtle">
        <div ref={setHost} className="area-control" />
        <ControlBandProvider host={host}>
          <div className="p-4 text-13 text-fg-muted">
            The page body declares the band; the layout row above renders it.
            <Controls />
          </div>
        </ControlBandProvider>
      </div>
    );
  },
};
