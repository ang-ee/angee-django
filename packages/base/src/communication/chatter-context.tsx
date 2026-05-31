import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

export const CHATTER_DEFAULT_WIDTH = 332;
export const CHATTER_MIN_WIDTH = 260;
export const CHATTER_MAX_WIDTH = 720;

export type ChatterTabId = "angee" | "comments" | "activity" | (string & {});

export interface ChatterContextValue {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  toggleCollapsed: () => void;
  width: number;
  setWidth: (width: number) => void;
  activeTab: ChatterTabId;
  setActiveTab: (tab: ChatterTabId) => void;
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
  const setWidth = useCallback((nextWidth: number) => {
    setRawWidth(clampWidth(nextWidth));
  }, []);
  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => !current);
  }, []);
  const value = useMemo<ChatterContextValue>(
    () => ({
      activeTab,
      collapsed,
      setActiveTab,
      setCollapsed,
      setWidth,
      toggleCollapsed,
      width,
    }),
    [activeTab, collapsed, setWidth, toggleCollapsed, width],
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

function clampWidth(width: number): number {
  if (!Number.isFinite(width)) return CHATTER_DEFAULT_WIDTH;
  return Math.min(CHATTER_MAX_WIDTH, Math.max(CHATTER_MIN_WIDTH, Math.round(width)));
}
