import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import type { CollapsiblePane } from "../page";

export type ChatterPaneController = Pick<CollapsiblePane, "collapsed" | "collapse" | "expand" | "toggle"> & {
  /** Native split panes publish their handle after the panel mounts. */
  ready?: boolean;
};

export type ChatterTabId = "agents" | "comments" | "activity" | (string & {});
export const CHATTER_TAB_SEARCH_KEY = "chatterTab";

export interface ChatterTab {
  id: ChatterTabId;
  label: ReactNode;
  icon?: string;
  count?: number;
  panelClassName?: string;
  children: ReactNode;
}

export interface ChatterContent {
  tabs?: readonly ChatterTab[];
  composer?: ReactNode;
}

export interface ChatterContextValue {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  toggleCollapsed: () => void;
  activeTab: ChatterTabId;
  /** Record a user or route-selected tab as the persistent shell intent. */
  setActiveTab: (tab: ChatterTabId) => void;
  /** Select initial asynchronous content only before the shell has explicit tab intent. */
  setInitialActiveTab: (tab: ChatterTabId) => void;
  content: ChatterContent | null;
  setContent: (owner: symbol, content: ChatterContent | null) => void;
  /** A record can place this same chatter below its form and reserve the aside for a native peek. */
  recordSupportKey: string | null;
  setRecordSupportKey: (owner: symbol, key: string | null) => void;
  recordPreview: ReactNode | null;
  setRecordPreview: (owner: symbol, key: string, node: ReactNode | null) => void;
  /**
   * Cross-tree collapse bridge for the shell's secondary pane (chatter or
   * record preview). The Workbench owns size and persistence; standalone hosts
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
  activeTab: "agents",
  setActiveTab: () => undefined,
  setInitialActiveTab: () => undefined,
  content: null,
  setContent: () => undefined,
  recordSupportKey: null,
  setRecordSupportKey: () => undefined,
  recordPreview: null,
  setRecordPreview: () => undefined,
  registerSecondaryController: () => undefined,
});

export function ChatterProvider({
  children,
  defaultCollapsed = false,
  defaultTab = "agents",
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
  const setActiveTab = useCallback((tab: ChatterTabId) => {
    explicitTabIntentRef.current = true;
    setActiveTabState(tab);
  }, []);
  const setInitialActiveTab = useCallback((tab: ChatterTabId) => {
    if (!explicitTabIntentRef.current) setActiveTabState(tab);
  }, []);
  const [contentState, setContentState] = useState<
    readonly (ChatterContent & { owner: symbol })[]
  >([]);
  const [support, setSupport] = useState<{ owner: symbol; key: string } | null>(null);
  const [preview, setPreview] = useState<{ owner: symbol; key: string; node: ReactNode } | null>(null);
  const setRecordSupportKey = useCallback((owner: symbol, key: string | null) => {
    setSupport((current) => key === null
      ? current?.owner === owner ? null : current
      : current?.owner === owner && current.key === key ? current : { owner, key });
  }, []);
  const setRecordPreview = useCallback((owner: symbol, key: string, node: ReactNode | null) => {
    setPreview((current) => node === null
      ? current?.owner === owner ? null : current
      : current?.owner === owner && current.key === key && Object.is(current.node, node)
        ? current
        : { owner, key, node });
  }, []);

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
        const previous = current.find((entry) => entry.owner === owner);
        if (next) {
          if (previous && sameChatterContent(previous, next)) {
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
    const composer = contentState.findLast((entry) => entry.composer !== undefined)?.composer;
    return {
      tabs: contentState.flatMap((entry) => entry.tabs ?? []),
      ...(composer !== undefined
        ? { composer }
        : {}),
    };
  }, [contentState]);

  const collapsed = controllerCollapsed ?? localCollapsed;
  const recordSupportKey = support?.key ?? null;
  // Never show the preceding record's source while the next record mounts.
  const recordPreview = preview?.key === recordSupportKey ? preview.node : null;
  const value = useMemo<ChatterContextValue>(
    () => ({
      activeTab,
      collapsed,
      content,
      recordSupportKey,
      recordPreview,
      registerSecondaryController,
      setActiveTab,
      setInitialActiveTab,
      setCollapsed,
      setContent,
      setRecordSupportKey,
      setRecordPreview,
      toggleCollapsed,
    }),
    [
      activeTab,
      collapsed,
      content,
      recordSupportKey,
      recordPreview,
      registerSecondaryController,
      setActiveTab,
      setCollapsed,
      setContent,
      setInitialActiveTab,
      setRecordSupportKey,
      setRecordPreview,
      toggleCollapsed,
    ],
  );
  return (
    <ChatterContext.Provider value={value}>
      {children}
    </ChatterContext.Provider>
  );
}

export function useChatter(): ChatterContextValue {
  return useContext(ChatterContext);
}

/** Select below-form support for one mounted record. Pass null for nested/read-only peeks. */
export function useRecordSupportPlacement(recordKey: string | null): void {
  const ownerRef = useRef<symbol | null>(null);
  if (ownerRef.current === null) ownerRef.current = Symbol("record-support");
  const owner = ownerRef.current;
  const { setRecordSupportKey } = useChatter();
  useLayoutEffect(() => {
    setRecordSupportKey(owner, recordKey);
    return () => setRecordSupportKey(owner, null);
  }, [owner, recordKey, setRecordSupportKey]);
}

/**
 * Publish secondary-pane content for the lifetime of the calling component.
 * Pass a memoized `content` object; inline objects or tab arrays republish on
 * every render and can churn the shell.
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
  if (content.tabs?.length === 0 && content.composer === undefined) return null;
  return content;
}

function sameChatterContent(
  current: (ChatterContent & { owner: symbol }) | null,
  next: ChatterContent,
): boolean {
  return current?.tabs === next.tabs && current?.composer === next.composer;
}
