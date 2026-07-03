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

import type { CollapsiblePane } from "../page";

export type ChatterTabId = "agents" | "comments" | "activity" | (string & {});

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
  setActiveTab: (tab: ChatterTabId) => void;
  content: ChatterContent | null;
  setContent: (owner: symbol, content: ChatterContent | null) => void;
  /**
   * Cross-tree collapse bridge. When a `Workbench` is mounted it registers its
   * secondary (chatter) pane's collapse controller here, so the chrome `TopBar`
   * toggle drives — and reflects — that pane (which owns size + collapse +
   * persistence). With no Workbench (standalone/tests) the context falls back to
   * a local `collapsed` flag. Pass `null` to unregister on unmount.
   */
  registerSecondaryController: (controller: CollapsiblePane | null) => void;
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
  content: null,
  setContent: () => undefined,
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
  const controllerRef = useRef<CollapsiblePane | null>(null);
  const [controllerCollapsed, setControllerCollapsed] = useState<
    boolean | null
  >(null);
  const [activeTab, setActiveTab] = useState<ChatterTabId>(defaultTab);
  const [contentState, setContentState] = useState<
    (ChatterContent & { owner: symbol }) | null
  >(null);

  const registerSecondaryController = useCallback(
    (controller: CollapsiblePane | null) => {
      controllerRef.current = controller;
      // Same-value state updates bail out, so the Workbench may republish its
      // controller every render (its identity changes each tick) without looping.
      setControllerCollapsed(controller ? controller.collapsed : null);
    },
    [],
  );

  const setCollapsed = useCallback((next: boolean) => {
    const controller = controllerRef.current;
    if (controller) {
      if (next) controller.collapse();
      else controller.expand();
    } else {
      setLocalCollapsed(next);
    }
  }, []);
  const toggleCollapsed = useCallback(() => {
    const controller = controllerRef.current;
    if (controller) controller.toggle();
    else setLocalCollapsed((current) => !current);
  }, []);
  const setContent = useCallback(
    (owner: symbol, content: ChatterContent | null) => {
      const next = normalizeChatterContent(content);
      setContentState((current) => {
        if (next) {
          if (current?.owner === owner && sameChatterContent(current, next)) {
            return current;
          }
          return { ...next, owner };
        }
        return current?.owner === owner ? null : current;
      });
    },
    [],
  );
  const content = useMemo<ChatterContent | null>(() => {
    if (!contentState) return null;
    return {
      ...(contentState.tabs !== undefined ? { tabs: contentState.tabs } : {}),
      ...(contentState.composer !== undefined
        ? { composer: contentState.composer }
        : {}),
    };
  }, [contentState]);

  const collapsed = controllerCollapsed ?? localCollapsed;
  const value = useMemo<ChatterContextValue>(
    () => ({
      activeTab,
      collapsed,
      content,
      registerSecondaryController,
      setActiveTab,
      setCollapsed,
      setContent,
      toggleCollapsed,
    }),
    [
      activeTab,
      collapsed,
      content,
      registerSecondaryController,
      setCollapsed,
      setContent,
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
