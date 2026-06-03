import * as React from "react";
import { createPortal } from "react-dom";

import { cn } from "../lib/cn";

// Host node for the shell's control band, with three meaningful states:
//   undefined   — no ConsoleShell above (standalone/test) → render the band inline.
//   null        — shell present but the area-control host has not mounted yet →
//                 render nothing this frame (the host arrives via state next commit).
//   HTMLElement — portal the band into the host.
const ControlBandContext =
  React.createContext<HTMLElement | null | undefined>(undefined);

export const controlBandItemClassName =
  "h-full min-h-0 flex-1 border-b-0 bg-transparent px-0 py-0";

export interface ControlBandProviderProps {
  children: React.ReactNode;
  host: HTMLElement | null;
}

export function ControlBandProvider({
  children,
  host,
}: ControlBandProviderProps): React.ReactElement {
  return (
    <ControlBandContext.Provider value={host}>
      {children}
    </ControlBandContext.Provider>
  );
}

export interface ControlBandProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * Renders its children as the page's flush control band in the shell's
 * `area-control` row. Under a `ConsoleShell` it portals into that row; with no
 * shell above (standalone/test) it renders the band inline.
 */
export function ControlBand({
  children,
  className,
}: ControlBandProps): React.ReactElement | React.ReactPortal | null {
  const host = React.useContext(ControlBandContext);
  const band = (
    <div
      className={cn(
        "flex h-control-h items-center gap-3 border-b border-border-subtle bg-sheet px-4",
        className,
      )}
    >
      {children}
    </div>
  );

  // See ControlBandContext: element → portal; undefined → inline; null → nothing yet.
  if (host) return createPortal(band, host);
  return host === undefined ? band : null;
}
