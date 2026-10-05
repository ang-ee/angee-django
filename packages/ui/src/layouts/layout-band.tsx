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
    /** Keep the parent host and registry without replacing this subtree. */
    inherit?: boolean;
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
    inherit = false,
  }: {
    children: React.ReactNode;
    host: HTMLElement | null | undefined;
    inherit?: boolean;
  }): React.ReactElement {
    const parent = React.useContext(BandContext);
    const registry = React.useMemo(createBandRegistry, []);
    const value = React.useMemo(() => ({ host, registry }), [host, registry]);
    return <BandContext.Provider value={inherit ? parent : value}>{children}</BandContext.Provider>;
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
    const node = React.useRef<HTMLElement>(null);
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
    // A shared host row is claimed only by a solitary band; siblings all
    // render inline beside their own sections. Until this band's own
    // registration lands (count 0), keep the solitary assumption.
    const portalHost = context?.host && count <= 1 ? context.host : null;
    // React hides a suspended page (the previous route while the next one
    // loads) by styling the page's own top-level nodes, which never include a
    // band portaled into the layout row. Layout effects are torn down while
    // their subtree is hidden and re-run when it reappears, so the portaled
    // band follows its page out of sight and out of reach.
    React.useLayoutEffect(() => {
      const band = node.current;
      if (!portalHost || !band) return undefined;
      band.style.removeProperty("display");
      return () => band.style.setProperty("display", "none", "important");
    }, [portalHost]);
    const band = React.createElement(
      element,
      { className: cn(baseClassName, className), ref: node },
      children,
    );
    if (!context) return band; // no provider (standalone/test) → inline
    if (portalHost) return createPortal(band, portalHost);
    if (context.host === undefined || count > 1) return band;
    return null; // host === null and solitary: host row mounts next commit.
  }

  return { Provider, Band };
}

const noopSubscribe = () => () => {};
const zero = () => 0;
