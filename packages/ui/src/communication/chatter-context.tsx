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
import { useRouter } from "@tanstack/react-router";

import type { CollapsiblePane } from "../page";
import { developmentMode } from "../lib/development-mode";
import type { ChatterSelection } from "../runtime/contracts";
import {
  CHATTER_FOCUS_SEARCH_KEY,
  CHATTER_FROM_SEARCH_KEY,
  CHATTER_SEARCH_KEYS,
  CHATTER_TAB_SEARCH_KEY,
  CHATTER_TARGET_SEARCH_KEY,
} from "../runtime/route-href";

export type ChatterPaneController = Pick<CollapsiblePane, "collapsed" | "collapse" | "expand" | "toggle"> & {
  /** Native split panes publish their handle after the panel mounts. */
  ready?: boolean;
};

export type ChatterTabId = string;
export { CHATTER_TAB_SEARCH_KEY, CHATTER_TARGET_SEARCH_KEY, CHATTER_FOCUS_SEARCH_KEY, CHATTER_FROM_SEARCH_KEY };

export interface ChatterTab {
  id: ChatterTabId;
  label: ReactNode;
  icon?: string;
  count?: number;
  panelClassName?: string;
  /** In the tab strip (the default); a drill-in is reached from a line, with a target. */
  strip?: boolean;
  children: ReactNode;
}

export interface ChatterContent {
  tabs?: readonly ChatterTab[];
}

export type { ChatterSelection };

export interface ChatterOpenOptions extends ChatterSelection {
  /** The tab that opened this one, so the aside offers the way back. */
  from?: ChatterTabId;
}

export interface ChatterContextValue {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  toggleCollapsed: () => void;
  activeTab: ChatterTabId;
  /**
   * Record a user or route-selected tab as the persistent shell intent. With
   * `from`, `target` or `focus` the choice is navigation state: it is written to
   * the URL (`chatterTab`, `chatterTarget`, `chatterFocus`, `chatterFrom`), so
   * links, reload and the browser's Back restore it.
   */
  setActiveTab: (tab: ChatterTabId, options?: ChatterOpenOptions) => void;
  /** Suggest asynchronous content; urgent attention may force it without recording user intent. */
  setInitialActiveTab: (tab: ChatterTabId, force?: boolean) => void;
  content: ChatterContent | null;
  setContent: (owner: symbol, content: ChatterContent | null) => void;
  /**
   * Cross-tree collapse bridge for the shell's Chatter pane. The Workbench owns
   * size and persistence; standalone hosts
   * fall back to a local `collapsed` flag. Pass `null` on unmount.
   */
  registerSecondaryController: (controller: ChatterPaneController | null) => void;
}

export interface ChatterProviderProps {
  children: ReactNode;
  defaultCollapsed?: boolean;
  defaultTab?: ChatterTabId;
}

const ChatterContext = createContext<ChatterContextValue>({
  collapsed: false,
  setCollapsed: () => undefined,
  toggleCollapsed: () => undefined,
  activeTab: "",
  setActiveTab: () => undefined,
  setInitialActiveTab: () => undefined,
  content: null,
  setContent: () => undefined,
  registerSecondaryController: () => undefined,
});

/**
 * @kind null
 * @reason Provider infrastructure: Chatter context.
 */
export function ChatterProvider({
  children,
  defaultCollapsed = false,
  defaultTab = "",
}: ChatterProviderProps): ReactElement {
  const [localCollapsed, setLocalCollapsed] = useState(defaultCollapsed);
  // The registered secondary pane controller (the imperative handle to toggle),
  // plus its reactive collapsed flag mirrored into state so the chrome re-renders
  // when the pane collapses (including via drag). `null` collapsed means no
  // controller is registered, so the chrome falls back to `localCollapsed`.
  const controllerRef = useRef<ChatterPaneController | null>(null);
  const desiredCollapsedRef = useRef(defaultCollapsed);
  const pendingCollapsedRef = useRef<boolean | null>(null);
  const registeredControllerRef = useRef(false);
  const [controllerCollapsed, setControllerCollapsed] = useState<
    boolean | null
  >(null);
  const [activeTab, setActiveTabState] = useState<ChatterTabId>(defaultTab);
  // The provider spans record routes, so tab intent does too. A shell remount is
  // the only reset; late route content must not replace a user or URL choice.
  const explicitTabIntentRef = useRef(false);
  // Standalone hosts (stories, bare tests) have no router: the choice stays local.
  const router = useRouter({ warn: false });
  const setActiveTab = useCallback((tab: ChatterTabId, options?: ChatterOpenOptions) => {
    explicitTabIntentRef.current = true;
    setActiveTabState(tab);
    if (!router) return;
    const search = router.state.location.search as Record<string, unknown>;
    const drill = options?.from !== undefined || options?.target !== undefined || options?.focus !== undefined;
    // A plain pick with nothing in the URL stays local; otherwise the URL is the
    // navigation state and must agree (a drill-in pushes, a plain pick replaces).
    if (!drill && !CHATTER_SEARCH_KEYS.some((key) => search[key] != null)) return;
    const next: Record<string, string | undefined> = {
      [CHATTER_TAB_SEARCH_KEY]: tab,
      [CHATTER_TARGET_SEARCH_KEY]: options?.target,
      [CHATTER_FOCUS_SEARCH_KEY]: options?.focus,
      [CHATTER_FROM_SEARCH_KEY]: options?.from,
    };
    // Adopting what the URL already says is not a navigation.
    if (CHATTER_SEARCH_KEYS.every((key) => (search[key] ?? undefined) === next[key])) return;
    void router.navigate({
      to: ".",
      replace: !drill,
      search: (previous: Record<string, unknown>) => ({ ...previous, ...next }),
    } as never);
  }, [router]);
  const setInitialActiveTab = useCallback((tab: ChatterTabId, force = false) => {
    if (force || !explicitTabIntentRef.current) setActiveTabState(tab);
  }, []);
  const [contentState, setContentState] = useState<
    readonly (ChatterContent & { owner: symbol })[]
  >([]);

  const registerSecondaryController = useCallback(
    (controller: ChatterPaneController | null) => {
      controllerRef.current = controller;
      if (controller && controller.ready !== false) {
        const pending = pendingCollapsedRef.current;
        pendingCollapsedRef.current = null;
        if (pending !== null && pending !== controller.collapsed) {
          if (pending) controller.collapse();
          else controller.expand();
        } else if (pending === null && !registeredControllerRef.current && !desiredCollapsedRef.current && controller.collapsed) {
          controller.expand();
        }
        registeredControllerRef.current = true;
      }
      // Same-value state updates bail out, so the Workbench may republish its
      // controller every render (its identity changes each tick) without looping.
      setControllerCollapsed(controller ? controller.collapsed : null);
    },
    [],
  );

  const setCollapsed = useCallback((next: boolean) => {
    desiredCollapsedRef.current = next;
    const controller = controllerRef.current;
    if (controller && controller.ready !== false) {
      pendingCollapsedRef.current = null;
      if (next) controller.collapse();
      else controller.expand();
    } else {
      pendingCollapsedRef.current = next;
      setLocalCollapsed(next);
    }
  }, []);
  const toggleCollapsed = useCallback(() => {
    const controller = controllerRef.current;
    if (controller && controller.ready !== false) {
      pendingCollapsedRef.current = null;
      desiredCollapsedRef.current = !controller.collapsed;
      controller.toggle();
    } else setLocalCollapsed((current) => {
      desiredCollapsedRef.current = !current;
      pendingCollapsedRef.current = !current;
      return !current;
    });
  }, []);
  const setContent = useCallback(
    (owner: symbol, content: ChatterContent | null) => {
      const next = normalizeChatterContent(content);
      setContentState((current) => {
        if (next && developmentMode()) {
          for (const tab of next.tabs ?? []) {
            if (current.some((entry) => entry.owner !== owner && entry.tabs?.some((other) => other.id === tab.id)))
              throw new Error(`Two chatter owners publish tab ${tab.id}.`);
          }
        }
        const previous = current.find((entry) => entry.owner === owner);
        if (next) {
          if (previous && previous.tabs === next.tabs) {
            return current;
          }
          const entry = { ...next, owner };
          // Replace an existing owner's entry in place so a republish never
          // reorders the merged tab strip; only a new owner is appended.
          return previous
            ? current.map((existing) => (existing.owner === owner ? entry : existing))
            : [...current, entry];
        }
        return previous ? current.filter((entry) => entry.owner !== owner) : current;
      });
    },
    [],
  );
  const content = useMemo<ChatterContent | null>(() => {
    if (!contentState.length) return null;
    return { tabs: contentState.flatMap((entry) => entry.tabs ?? []) };
  }, [contentState]);

  const collapsed = controllerCollapsed ?? localCollapsed;
  const value = useMemo<ChatterContextValue>(
    () => ({
      activeTab,
      collapsed,
      content,
      registerSecondaryController,
      setActiveTab,
      setInitialActiveTab,
      setCollapsed,
      setContent,
      toggleCollapsed,
    }),
    [
      activeTab,
      collapsed,
      content,
      registerSecondaryController,
      setActiveTab,
      setCollapsed,
      setContent,
      setInitialActiveTab,
      toggleCollapsed,
    ],
  );
  return (
    <ChatterContext.Provider value={value}>
      {children}
    </ChatterContext.Provider>
  );
}

/**
 * @owner Chatter
 */
export function useChatter(): ChatterContextValue {
  return useContext(ChatterContext);
}

/**
 * Publish secondary-pane content for the lifetime of the calling component.
 * Pass a memoized `content` object; inline objects or tab arrays republish on
 * every render and can churn the shell.
 * @owner Chatter
 */
export function useChatterContent(content: ChatterContent | null): void {
  const ownerRef = useRef<symbol | null>(null);
  if (ownerRef.current === null) ownerRef.current = Symbol("chatter-content");
  const owner = ownerRef.current;
  const { setContent } = useChatter();
  useEffect(() => {
    setContent(owner, content);
    return () => setContent(owner, null);
  }, [content, owner, setContent]);
}

function normalizeChatterContent(content: ChatterContent | null): ChatterContent | null {
  if (content === null) return null;
  if (content.tabs?.length === 0) return null;
  return content;
}
