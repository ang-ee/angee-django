import { useState, type ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { drawerVariants } from "../ui/drawer";
import { useDrawers, type DrawerEdge } from "../runtime";
import { drawerPanelId, useDrawerState } from "./drawer-context";

/**
 * The non-modal overlay for one edge: renders the active drawer's `render()` in a
 * plain `fixed`, edge-anchored panel reusing the `drawerVariants` slide
 * transforms — **not** the Base UI `Drawer` dialog, so there is no scrim and no
 * focus trap (the page stays usable; JetBrains "Undock"). Mounted once at shell
 * level, and a drawer once opened stays mounted while hidden, so its content
 * survives route changes and closing the edge (an agent chat keeps its
 * transcript). `Esc` is not required to return focus (non-modal).
 */
export function DrawerOverlay({
  edge,
}: {
  edge: DrawerEdge;
}): ReactElement | null {
  const drawers = useDrawers(edge);
  const { openId, close } = useDrawerState();
  const t = useUiT();
  const [visited, setVisited] = useState<readonly string[]>([]);

  const active = drawers.find((drawer) => drawer.id === openId(edge));
  if (active && !visited.includes(active.id)) setVisited([...visited, active.id]);
  const mounted = drawers.filter((drawer) => drawer === active || visited.includes(drawer.id));
  if (mounted.length === 0) return null;

  const styles = drawerVariants({ side: edge });
  return (
    <aside
      id={drawerPanelId(edge)}
      role="complementary"
      aria-label={active?.title}
      // The bare `hidden` attribute, with the display utility dropped, removes a
      // closed edge from layout and the a11y tree while its drawers stay mounted.
      hidden={!active}
      // `z-drawer` overrides the recipe's `z-modal` (tailwind-merge keeps the
      // last z-utility), keeping the panel above chrome but below modals.
      className={styles.content({ className: active ? "z-drawer" : "z-drawer hidden" })}
    >
      <div className={styles.header({ className: "flex items-center justify-between" })}>
        <span>{active?.title}</span>
        <button
          type="button"
          aria-label={t("drawer.close")}
          className="rounded-6 p-1 text-fg-muted outline-none transition-colors hover:bg-sheet-2 hover:text-fg focus-visible:focus-ring"
          onClick={() => close(edge)}
        >
          <Glyph decorative name="x" />
        </button>
      </div>
      {mounted.map((drawer) => (
        <div key={drawer.id} hidden={drawer !== active} className={styles.body({ className: "min-h-0 flex-1" })}>
          {drawer.render()}
        </div>
      ))}
    </aside>
  );
}
