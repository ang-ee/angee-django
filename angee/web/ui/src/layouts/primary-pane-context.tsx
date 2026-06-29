import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

/**
 * The primary-pane publish seam. Mirrors the chatter-context content seam
 * (`useChatterContent`/`ChatterProvider`): a page publishes a node into the
 * console shell's `primary` (left explorer) pane for its lifetime, and the shell
 * renders it in the `Workbench` primary pane. One owner symbol per caller —
 * publish on mount, clear on unmount — so unrelated callers never clobber each
 * other and a stale publisher never wins. When nothing is published the value is
 * `null` and the shell falls back to its own primary content (the settings
 * sub-nav, if any).
 */
export interface PrimaryPaneContextValue {
  /** The currently published primary-pane node, or `null` when nothing is published. */
  node: ReactNode | null;
  /**
   * Publish (`node` non-null) or clear (`node === null`) the primary pane for one
   * `owner`. Last writer owns; a clear only takes effect if `owner` is the current
   * publisher, so an unmounting page never erases a node a newer page just set.
   */
  setNode: (owner: symbol, node: ReactNode | null) => void;
}

const PrimaryPaneContext = createContext<PrimaryPaneContextValue>({
  node: null,
  setNode: () => undefined,
});

export function PrimaryPaneProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  const [state, setState] = useState<{ owner: symbol; node: ReactNode } | null>(
    null,
  );
  const setNode = useCallback((owner: symbol, node: ReactNode | null) => {
    setState((current) => {
      if (node != null) return { owner, node };
      // A clear only wins if this owner is the current publisher.
      return current?.owner === owner ? null : current;
    });
  }, []);
  const node = state ? state.node : null;
  const value = useMemo<PrimaryPaneContextValue>(
    () => ({ node, setNode }),
    [node, setNode],
  );
  return (
    <PrimaryPaneContext.Provider value={value}>
      {children}
    </PrimaryPaneContext.Provider>
  );
}

/** Read the published primary-pane node (and the publish seam). The shell reads
 * `node`; tests can render it into a thin host to assert what a page published. */
export function usePrimaryPaneContent(): PrimaryPaneContextValue {
  return useContext(PrimaryPaneContext);
}

/**
 * Publish `node` into the console shell's primary (left explorer) pane for the
 * lifetime of the calling component. The symmetric twin of `useChatterContent`:
 * publish on mount, clear on unmount.
 *
 * Pass a **memoized** node (like `NotePage` memoizes its chatter content with
 * `useMemo`) — the publish effect re-runs whenever `node`'s identity changes, so
 * an inline `<Explorer />` would republish every render.
 */
export function usePrimaryPane(node: ReactNode | null): void {
  const ownerRef = useRef<symbol | null>(null);
  if (ownerRef.current === null) ownerRef.current = Symbol("primary-pane");
  const owner = ownerRef.current;
  const { setNode } = usePrimaryPaneContent();
  useEffect(() => {
    setNode(owner, node);
    return () => setNode(owner, null);
  }, [node, owner, setNode]);
}
