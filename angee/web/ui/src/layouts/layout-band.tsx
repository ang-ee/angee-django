import * as React from "react";
import { createPortal } from "react-dom";

import { cn } from "../lib/cn";

/**
 * One layout "portal band" — a flush bar a page renders into a `ConsoleLayout`
 * row. The host node carried by the band's context has three meaningful states:
 *   undefined   — no `ConsoleLayout` above (standalone/test) → render inline.
 *   null        — layout present but the host row has not mounted yet → render
 *                 nothing this frame (the host arrives via state next commit).
 *   HTMLElement — portal the band into the host row.
 *
 * `ControlBand` and `Statusline` are the two instances; they differ only in the
 * wrapper element and its flush styling, so the context/provider/portal logic
 * lives here once.
 */
export interface LayoutBand {
  Provider: (props: {
    children: React.ReactNode;
    host: HTMLElement | null | undefined;
  }) => React.ReactElement;
  Band: (props: {
    children: React.ReactNode;
    className?: string;
  }) => React.ReactElement | React.ReactPortal | null;
}

export function createLayoutBand(
  element: "div" | "footer",
  baseClassName: string,
): LayoutBand {
  const HostContext = React.createContext<HTMLElement | null | undefined>(
    undefined,
  );

  function Provider({
    children,
    host,
  }: {
    children: React.ReactNode;
    host: HTMLElement | null | undefined;
  }): React.ReactElement {
    return <HostContext.Provider value={host}>{children}</HostContext.Provider>;
  }

  function Band({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }): React.ReactElement | React.ReactPortal | null {
    const host = React.useContext(HostContext);
    const band = React.createElement(
      element,
      { className: cn(baseClassName, className) },
      children,
    );
    // element → portal; undefined → inline; null → nothing yet.
    if (host) return createPortal(band, host);
    return host === undefined ? band : null;
  }

  return { Provider, Band };
}
