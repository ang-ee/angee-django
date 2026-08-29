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
 * The host row belongs to a band only while it is the page's SOLITARY band: a
 * page composing several band-bearing views (two grouped lists on a "my work"
 * or roadmap page) must not stack portals into the one row, so the moment more
 * than one band is mounted under the same provider, every band renders inline
 * beside its own section instead. Subtrees that opt out through their own
 * provider (drawers, settings shells) register there, not here, and never
 * affect the page's count.
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

interface BandRegistry {
  register: (id: string) => () => void;
  subscribe: (listener: () => void) => () => void;
  count: () => number;
}

function createBandRegistry(): BandRegistry {
  const mounted = new Set<string>();
  const listeners = new Set<() => void>();
  const notify = () => {
    for (const listener of listeners) listener();
  };
  return {
    register: (id) => {
      mounted.add(id);
      notify();
      return () => {
        mounted.delete(id);
        notify();
      };
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    count: () => mounted.size,
  };
}

interface BandContextValue {
  host: HTMLElement | null | undefined;
  registry: BandRegistry;
}

export function createLayoutBand(
  element: "div" | "footer",
  baseClassName: string,
): LayoutBand {
  const BandContext = React.createContext<BandContextValue | undefined>(
    undefined,
  );

  function Provider({
    children,
    host,
  }: {
    children: React.ReactNode;
    host: HTMLElement | null | undefined;
  }): React.ReactElement {
    const registry = React.useMemo(createBandRegistry, []);
    const value = React.useMemo(() => ({ host, registry }), [host, registry]);
    return <BandContext.Provider value={value}>{children}</BandContext.Provider>;
  }

  function Band({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }): React.ReactElement | React.ReactPortal | null {
    const context = React.useContext(BandContext);
    const id = React.useId();
    const registry = context?.registry;
    // useLayoutEffect so a page mounting several bands settles to inline
    // before the browser paints the stacked-portal intermediate state.
    React.useLayoutEffect(() => {
      if (!registry) return undefined;
      return registry.register(id);
    }, [registry, id]);
    const count = React.useSyncExternalStore(
      registry?.subscribe ?? noopSubscribe,
      registry?.count ?? zero,
      registry?.count ?? zero,
    );
    const band = React.createElement(
      element,
      { className: cn(baseClassName, className) },
      children,
    );
    if (!context) return band; // no provider (standalone/test) → inline
    const { host } = context;
    // A shared host row is claimed only by a solitary band; siblings all
    // render inline beside their own sections. Until this band's own
    // registration lands (count 0), keep the solitary assumption.
    if (host && count <= 1) return createPortal(band, host);
    if (host === undefined || count > 1) return band;
    return null; // host === null and solitary: host row mounts next commit.
  }

  return { Provider, Band };
}

const noopSubscribe = () => () => {};
const zero = () => 0;
