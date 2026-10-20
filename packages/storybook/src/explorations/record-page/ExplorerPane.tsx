import * as React from "react";
import { Glyph } from "@angee/ui";
import { PaneToolbar, RootSelector, ScopeChip } from "./PaneToolbar";
import type { ExplorerData } from "./types";

/**
 * The left region: where am I, where next. A tree (drive, vault), a collection list (split
 * placement, with the list's own views) or a navigator (nexus, with lenses). It changes the middle
 * and never describes the record. One toolbar: root · view, lenses, search, create.
 */
export function ExplorerPane({ data }: { data: ExplorerData }): React.ReactElement {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border-subtle bg-sheet">
      <PaneToolbar
        selector={<RootSelector root={data.root} view={data.view} />}
        actions={data.create ? [{ icon: "plus", label: data.create }] : undefined}
        overflow={false}
      />
      {data.lenses ? (
        <div className="flex gap-1 border-b border-border-subtle px-2 py-1.5">{data.lenses.map((l) => <ScopeChip key={l.label} label={l.label} active={l.active} />)}</div>
      ) : null}
      <ul className="min-h-0 flex-1 overflow-y-auto py-1">
        {data.rows.map((row) => (
          <li key={row.label}>
            <a href="#" className={`flex items-center gap-2 py-1.5 pr-3 text-13 ${row.active ? "bg-canvas font-medium text-fg" : "text-fg hover:bg-canvas/60"}`} style={{ paddingLeft: `${12 + (row.depth ?? 0) * 14}px` }}>
              {row.icon ? <Glyph name={row.icon} /> : row.tone ? <span className={`size-1.5 rounded-full ${row.tone === "warning" ? "bg-warning-text" : "bg-success-text"}`} /> : <span className="size-1.5" />}
              <span className="min-w-0 flex-1 truncate">{row.label}</span>
              {row.meta ? <span className="shrink-0 text-11 tabular-nums text-fg-muted">{row.meta}</span> : null}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
