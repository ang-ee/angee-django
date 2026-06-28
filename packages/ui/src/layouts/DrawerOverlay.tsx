import type { ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { drawerVariants } from "../ui/drawer";
import { useDrawers, type DrawerEdge } from "../runtime";
import { drawerPanelId, useDrawerState } from "./drawer-context";

/**
 * The non-modal overlay for one edge: renders the active drawer's `render()` in a
 * plain `fixed`, edge-anchored panel reusing the `drawerVariants` slide
 * transforms — **not** the Base UI `Drawer` dialog, so there is no scrim and no
 * focus trap (the page stays usable; JetBrains "Undock"). Renders nothing when
 * the edge is closed. Mounted once at shell level so its content survives route
 * changes. `Esc` is not required to return focus (non-modal).
 */
export function DrawerOverlay({
  edge,
}: {
  edge: DrawerEdge;
}): ReactElement | null {
  const drawers = useDrawers(edge);
  const { openId, close } = useDrawerState();
  const t = useBaseT();

  const active = drawers.find((drawer) => drawer.id === openId(edge));
  if (!active) return null;

  const styles = drawerVariants({ side: edge });
  return (
    <aside
      id={drawerPanelId(edge)}
      role="complementary"
      aria-label={active.title}
      // `z-drawer` overrides the recipe's `z-modal` (tailwind-merge keeps the
      // last z-utility), keeping the panel above chrome but below modals.
      className={styles.content({ className: "z-drawer" })}
    >
      <div className={styles.header({ className: "flex items-center justify-between" })}>
        <span>{active.title}</span>
        <button
          type="button"
          aria-label={t("drawer.close")}
          className="rounded-6 p-1 text-fg-muted outline-none transition-colors hover:bg-sheet-2 hover:text-fg focus-visible:focus-ring"
          onClick={() => close(edge)}
        >
          <Glyph decorative name="x" />
        </button>
      </div>
      <div className={styles.body()}>{active.render()}</div>
    </aside>
  );
}
