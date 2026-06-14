import type * as React from "react";

import { createShellBand } from "./shell-band";

const band = createShellBand(
  "div",
  "flex h-control-h items-center gap-3 border-b border-border-subtle bg-sheet px-4",
);

export const controlBandItemClassName =
  "h-full min-h-0 flex-1 border-b-0 bg-transparent px-0 py-0";

export interface ControlBandProviderProps {
  children: React.ReactNode;
  /**
   * Where bands below render: an element to portal into, `null` while a shell's
   * host is still mounting, or `undefined` to force inline — the last is how a
   * band-bearing view inside a dialog opts out of the shell's band and keeps its
   * controls in the dialog.
   */
  host: HTMLElement | null | undefined;
}

/** Provide the control-band host (`area-control`) for the bands rendered below. */
export const ControlBandProvider: (
  props: ControlBandProviderProps,
) => React.ReactElement = band.Provider;

export interface ControlBandProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * Renders its children as the page's flush control band in the shell's
 * `area-control` row. Under a `ConsoleShell` it portals into that row; with no
 * shell above (standalone/test) it renders the band inline.
 */
export const ControlBand: (
  props: ControlBandProps,
) => React.ReactElement | React.ReactPortal | null = band.Band;
