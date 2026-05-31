import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { Link } from "@tanstack/react-router";

import { cn } from "../lib/cn";

export interface BreadcrumbItem {
  label: ReactNode;
  to?: string;
}

export interface BreadcrumbContextValue {
  items: readonly BreadcrumbItem[];
  setItems: (items: readonly BreadcrumbItem[]) => void;
}

export interface BreadcrumbProviderProps {
  children: ReactNode;
  initialTrail?: readonly BreadcrumbItem[];
  items?: readonly BreadcrumbItem[];
}

const DEFAULT_ITEMS: readonly BreadcrumbItem[] = [{ label: "Console" }];
const BreadcrumbContext = createContext<BreadcrumbContextValue>({
  items: DEFAULT_ITEMS,
  setItems: () => undefined,
});

export function BreadcrumbProvider({
  children,
  initialTrail,
  items,
}: BreadcrumbProviderProps): ReactElement {
  const seed = items ?? initialTrail ?? DEFAULT_ITEMS;
  const [trail, setTrail] = useState<readonly BreadcrumbItem[]>(seed);

  useEffect(() => {
    setTrail(seed);
  }, [seed]);

  const value = useMemo<BreadcrumbContextValue>(
    () => ({ items: trail, setItems: setTrail }),
    [trail],
  );

  return (
    <BreadcrumbContext.Provider value={value}>
      {children}
    </BreadcrumbContext.Provider>
  );
}

export function useBreadcrumb(): BreadcrumbContextValue {
  return useContext(BreadcrumbContext);
}

export interface BreadcrumbProps {
  className?: string;
}

export function Breadcrumb({ className }: BreadcrumbProps): ReactElement {
  const { items } = useBreadcrumb();
  return (
    <nav
      aria-label="Breadcrumb"
      className={cn(
        "area-crumbs z-breadcrumb flex h-crumbs-h min-w-0 items-center gap-1 border-b border-border-subtle bg-sheet px-4 text-13 text-fg-muted",
        className,
      )}
    >
      {items.map((item, index) => {
        const current = index === items.length - 1;
        const key = `${String(item.label)}:${index}`;
        return (
          <span key={key} className="contents">
            {index > 0 ? (
              <span aria-hidden className="shrink-0 text-fg-subtle">
                /
              </span>
            ) : null}
            {item.to && !current ? (
              <Link
                to={item.to}
                className="min-w-0 truncate rounded-sm outline-none hover:text-fg focus-visible:focus-ring"
              >
                {item.label}
              </Link>
            ) : (
              <span
                aria-current={current ? "page" : undefined}
                className="min-w-0 truncate font-medium text-fg"
              >
                {item.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
