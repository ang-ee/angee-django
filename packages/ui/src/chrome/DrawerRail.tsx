import type { ReactElement } from "react";

import { useBaseT } from "../i18n";
import { tv } from "../lib/variants";
import { drawerPanelId, useDrawerState } from "../layouts/drawer-context";
import { useDrawers, type DrawerEdge } from "../runtime";
import { Glyph } from "./Glyph";

const drawerRailVariants = tv({
  slots: {
    // A thin stripe of tabs pinned to one edge, above the content (z-drawer).
    rail: "fixed z-drawer flex gap-1 p-1",
    tab: "flex items-center gap-1.5 rounded-6 border border-border-subtle bg-sheet px-2 py-1.5 text-2xs text-fg-muted shadow-sm outline-none transition-colors hover:bg-sheet-2 focus-visible:focus-ring aria-pressed:bg-sheet-2 aria-pressed:text-fg",
  },
  variants: {
    edge: {
      // Right: a vertical column centered on the right edge; tabs read bottom-up
      // (the "Feedback tab" look).
      right: {
        rail: "right-0 top-1/2 -translate-y-1/2 flex-col rounded-l-8",
        tab: "[writing-mode:vertical-rl] rotate-180",
      },
      // Bottom: a horizontal row centered on the bottom edge.
      bottom: {
        rail: "bottom-0 left-1/2 -translate-x-1/2 flex-row rounded-t-8",
        tab: "",
      },
    },
  },
});

/**
 * The edge stripe-tabs that pull out an edge's drawers — one tab per registered
 * drawer (`useDrawers(edge)`), toggling the shared `DrawerProvider` state.
 * Renders nothing when the edge has no contributed drawers. JetBrains-style:
 * every drawer toggle lives on the edge stripe (panes toggle from the TopBar).
 */
export function DrawerRail({ edge }: { edge: DrawerEdge }): ReactElement | null {
  const drawers = useDrawers(edge);
  const { openId, toggle } = useDrawerState();
  const t = useBaseT();
  const styles = drawerRailVariants({ edge });

  if (drawers.length === 0) return null;

  const active = openId(edge);
  return (
    <div
      role="toolbar"
      aria-label={t(`drawer.rail.${edge}`)}
      className={styles.rail()}
    >
      {drawers.map((drawer) => {
        const isOpen = active === drawer.id;
        return (
          <button
            key={drawer.id}
            type="button"
            aria-pressed={isOpen}
            aria-expanded={isOpen}
            aria-controls={drawerPanelId(edge)}
            className={styles.tab()}
            onClick={() => toggle(edge, drawer.id)}
          >
            {drawer.icon ? <Glyph decorative name={drawer.icon} /> : null}
            <span>{drawer.title}</span>
          </button>
        );
      })}
    </div>
  );
}
