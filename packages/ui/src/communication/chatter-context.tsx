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

export const CHATTER_DEFAULT_WIDTH = 332;
export const CHATTER_MIN_WIDTH = 260;
export const CHATTER_MAX_WIDTH = 720;

export type ChatterTabId = "angee" | "comments" | "activity" | (string & {});

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
  width: number;
  setWidth: (width: number) => void;
  activeTab: ChatterTabId;
  setActiveTab: (tab: ChatterTabId) => void;
  content: ChatterContent | null;
  setContent: (owner: symbol, content: ChatterContent | null) => void;
}

export interface ChatterProviderProps {
  children: ReactNode;
  defaultCollapsed?: boolean;
  defaultWidth?: number;
  defaultTab?: ChatterTabId;
}

const ChatterContext = createContext<ChatterContextValue>({
  collapsed: false,
  setCollapsed: () => undefined,
  toggleCollapsed: () => undefined,
  width: CHATTER_DEFAULT_WIDTH,
  setWidth: () => undefined,
  activeTab: "angee",
  setActiveTab: () => undefined,
  content: null,
  setContent: () => undefined,
});

export function ChatterProvider({
  children,
  defaultCollapsed = false,
  defaultWidth = CHATTER_DEFAULT_WIDTH,
  defaultTab = "angee",
}: ChatterProviderProps): ReactElement {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [width, setRawWidth] = useState(() => clampWidth(defaultWidth));
  const [activeTab, setActiveTab] = useState<ChatterTabId>(defaultTab);
  const [contentState, setContentState] = useState<
    (ChatterContent & { owner: symbol }) | null
  >(null);
  const setWidth = useCallback((nextWidth: number) => {
    setRawWidth(clampWidth(nextWidth));
  }, []);
  const setContent = useCallback(
    (owner: symbol, content: ChatterContent | null) => {
      setContentState((current) => {
        if (content) return { ...content, owner };
        return current?.owner === owner ? null : current;
      });
    },
    [],
  );
  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => !current);
  }, []);
  const content = useMemo<ChatterContent | null>(() => {
    if (!contentState) return null;
    return {
      ...(contentState.tabs !== undefined ? { tabs: contentState.tabs } : {}),
      ...(contentState.composer !== undefined
        ? { composer: contentState.composer }
        : {}),
    };
  }, [contentState]);
  const value = useMemo<ChatterContextValue>(
    () => ({
      activeTab,
      collapsed,
      content,
      setActiveTab,
      setCollapsed,
      setContent,
      setWidth,
      toggleCollapsed,
      width,
    }),
    [activeTab, collapsed, content, setContent, setWidth, toggleCollapsed, width],
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

function clampWidth(width: number): number {
  if (!Number.isFinite(width)) return CHATTER_DEFAULT_WIDTH;
  return Math.min(CHATTER_MAX_WIDTH, Math.max(CHATTER_MIN_WIDTH, Math.round(width)));
}
