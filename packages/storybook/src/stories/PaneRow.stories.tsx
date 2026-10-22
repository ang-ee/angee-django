import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { PaneLabel, PaneLabelAction, PaneRow, PaneRows, ScopeChip } from "@angee/ui";

/**
 * The pane line anatomy every side pane shares: glyph · title · detail · meta,
 * a muted second line, the row's one verb beside the row's own control, day and
 * section labels with counts, folds and one verb.
 */
const meta = {
  title: "Communication/PaneRow",
  component: PaneRow,
  parameters: { layout: "padded", owner: [PaneRow, PaneRows, PaneLabel, PaneLabelAction, ScopeChip] },
} satisfies Meta<typeof PaneRow>;

export default meta;

type Story = StoryObj;

const noop = () => undefined;

/** Rows as links and as buttons; a string verb hints what opening does, a node is the pane's own control. */
export const Default: Story = {
  render: () => (
    <div className="w-[26rem] rounded-6 border border-border-subtle bg-sheet">
      <PaneRows className="px-2 py-1">
        <PaneLabel>Today</PaneLabel>
        <li><PaneRow icon="play" tone="info" title="Invoice review" detail="waiting on you" meta="09:12" sub="Agent · 2 changes · 1 decision" needsMe href="#run" verb="Open run" label="Open run" /></li>
        <li><PaneRow icon="message-square" tone="muted" title="Cloudflare confirmed the PO number" meta="08:40" sub="Sofia Marin" onOpen={noop} verb="Open" /></li>
        <li><PaneRow icon="circle" title="Attach the signed agreement" detail="due Friday" sub="Alex Morgan" onOpen={noop} selected
          verb={<PaneLabelAction icon="check" onClick={noop}>Done</PaneLabelAction>} /></li>
        <PaneLabel>Yesterday</PaneLabel>
        <li><PaneRow icon="activity" tone="muted" title="Amount corrected" detail="€ 412.00 → € 418.60" meta="16:05" sub="admin" href="#change" verb="Open" /></li>
        <li><PaneRow icon="file" title="A row with no destination is static" sub="no link, no button" /></li>
      </PaneRows>
    </div>
  ),
};

/** Section labels: a count, a fold that opens and closes from the label, and the label's one verb. */
export const Labels: Story = {
  render: function Labels() {
    const [open, setOpen] = useState(true);
    return (
      <div className="w-[26rem] rounded-6 border border-border-subtle bg-sheet">
        <PaneRows className="px-2 py-1">
          <PaneLabel count={2} action={<PaneLabelAction icon="upload" onClick={noop}>Upload</PaneLabelAction>}>Files</PaneLabel>
          <li><PaneRow icon="file" title="v6-cloudflare-9311.xml" meta="5.8 KB" href="#file" /></li>
          <li><PaneRow icon="file" title="cloudflare-agreement.pdf" meta="412 KB" href="#file" /></li>
          <PaneLabel count={1} folded={!open} onToggle={() => setOpen((value) => !value)}>Completed</PaneLabel>
          {open ? <li><PaneRow icon="check" tone="success" title="Match the purchase order" sub="Sofia Marin" onOpen={noop} /></li> : null}
        </PaneRows>
      </div>
    );
  },
};

/** Scope chips: the pane's filter row, one active. */
export const Scopes: Story = {
  render: function Scopes() {
    const [scope, setScope] = useState("all");
    return (
      <div className="flex gap-1">
        <ScopeChip label="All" count={5} active={scope === "all"} onClick={() => setScope("all")} />
        <ScopeChip label="Needs you" count={1} tone="warning" active={scope === "needs"} onClick={() => setScope("needs")} />
        <ScopeChip label="Runs" count={2} active={scope === "runs"} onClick={() => setScope("runs")} />
      </div>
    );
  },
};
