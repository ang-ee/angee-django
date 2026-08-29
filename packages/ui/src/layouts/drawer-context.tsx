import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { layoutStorage } from "../page";
import type { DrawerEdge } from "../runtime";

/**
 * The drawer open/closed state. Mirrors `chatter-context`/`primary-pane-context`:
 * one provider holds, per edge, the id of the open drawer (or `null` = closed).
 * Each edge is independent (right + bottom open at once); within an edge a single
 * drawer is open, so switching tabs is "open another id". Persisted per edge to
 * `localStorage` through the same `layoutStorage()` owner the panes use, so a
 * drawer reopens where it was left. Mounted in `ConsoleLayout` above the
 * `console-grid`, so the open drawer's content survives route changes.
 */
export interface DrawerStateValue {
  /** The open drawer id for `edge`, or `null` when that edge is closed. */
  openId: (edge: DrawerEdge) => string | null;
  /** Open `id` on `edge` (replacing whatever was open there). */
  open: (edge: DrawerEdge, id: string) => void;
  /** Close `edge`. */
  close: (edge: DrawerEdge) => void;
  /** Open `id` on `edge`, or close the edge if `id` is already the open one. */
  toggle: (edge: DrawerEdge, id: string) => void;
}

type OpenByEdge = Record<DrawerEdge, string | null>;

const EDGES: readonly DrawerEdge[] = ["right", "bottom"];
const STORAGE_PREFIX = "angee.drawer.";

const DrawerContext = createContext<DrawerStateValue>({
  openId: () => null,
  open: () => undefined,
  close: () => undefined,
  toggle: () => undefined,
});

function storageKey(edge: DrawerEdge): string {
  return `${STORAGE_PREFIX}${edge}`;
}

function readOpenByEdge(): OpenByEdge {
  const storage = layoutStorage();
  const read = (edge: DrawerEdge): string | null => {
    if (!storage) return null;
    try {
      return storage.getItem(storageKey(edge));
    } catch {
      return null;
    }
  };
  return { right: read("right"), bottom: read("bottom") };
}

function persistOpenByEdge(state: OpenByEdge): void {
  const storage = layoutStorage();
  if (!storage) return;
  for (const edge of EDGES) {
    try {
      const id = state[edge];
      if (id == null) storage.removeItem(storageKey(edge));
      else storage.setItem(storageKey(edge), id);
    } catch {
      // Persistence is best-effort; ignore quota/privacy-mode failures.
    }
  }
}

export function DrawerProvider({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  const [openByEdge, setOpenByEdge] = useState<OpenByEdge>(readOpenByEdge);

  // Persist declaratively whenever the state changes, so the setters stay pure
  // (no side effects inside the state updater, which React may call twice).
  useEffect(() => {
    persistOpenByEdge(openByEdge);
  }, [openByEdge]);

  const open = useCallback((edge: DrawerEdge, id: string) => {
    setOpenByEdge((current) =>
      current[edge] === id ? current : { ...current, [edge]: id },
    );
  }, []);
  const close = useCallback((edge: DrawerEdge) => {
    setOpenByEdge((current) =>
      current[edge] === null ? current : { ...current, [edge]: null },
    );
  }, []);
  const toggle = useCallback((edge: DrawerEdge, id: string) => {
    setOpenByEdge((current) => ({
      ...current,
      [edge]: current[edge] === id ? null : id,
    }));
  }, []);
  const openId = useCallback(
    (edge: DrawerEdge) => openByEdge[edge],
    [openByEdge],
  );

  const value = useMemo<DrawerStateValue>(
    () => ({ openId, open, close, toggle }),
    [openId, open, close, toggle],
  );
  return (
    <DrawerContext.Provider value={value}>{children}</DrawerContext.Provider>
  );
}

/** Read the per-edge open drawer id and the open/close/toggle controls. */
export function useDrawerState(): DrawerStateValue {
  return useContext(DrawerContext);
}

/**
 * The DOM id of an edge's overlay panel — the one owner of the
 * `aria-controls` target, so a `DrawerRail` tab and the `DrawerOverlay` it opens
 * agree without duplicating the string.
 */
export function drawerPanelId(edge: DrawerEdge): string {
  return `drawer-panel-${edge}`;
}
