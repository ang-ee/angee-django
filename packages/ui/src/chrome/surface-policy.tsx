import { createContext, useContext, type ReactNode } from "react";

/** Omitted addresses keep their existing contributions; named lists restrict them. */
export interface SurfaceAdmission {
  slots?: Readonly<Record<string, readonly string[]>>;
  aside?: readonly string[];
  drawers?: readonly string[];
}

/** Shell and aside facts resolved by the app's inherited route policy. */
export interface SurfacePresentation {
  admit?: SurfaceAdmission;
  chatter?: "hidden" | { tabs?: readonly string[] };
  shell?: {
    breadcrumb?: boolean;
    commandSearch?: boolean;
    asideOpen?: boolean;
  };
}

const SurfaceContext = createContext<SurfacePresentation>({});

export function SurfacePresentationProvider({
  value,
  children,
}: {
  value: SurfacePresentation;
  children: ReactNode;
}): ReactNode {
  return <SurfaceContext.Provider value={value}>{children}</SurfaceContext.Provider>;
}

export function useSurfacePresentation(): SurfacePresentation {
  return useContext(SurfaceContext);
}

/** The active route's admission, including explicit empty lists. */
export function useSurfaceAdmission(): SurfaceAdmission | undefined {
  return useSurfacePresentation().admit;
}

/** Whether the active policy admits one contribution to a named slot. */
export function isSurfaceSlotAdmitted(
  admission: SurfaceAdmission | undefined,
  slot: string,
  id: string,
): boolean {
  const ids = admission?.slots?.[slot];
  return ids === undefined || ids.includes(id);
}

/** Preserve declared tab order while applying any named aside restriction. */
export function admittedAsideTabs(surface: SurfacePresentation): readonly string[] | undefined {
  const tabs = surface.chatter === "hidden" ? [] : surface.chatter?.tabs;
  const aside = surface.admit?.aside;
  if (aside === undefined) return tabs;
  return tabs === undefined ? aside : tabs.filter((id) => aside.includes(id));
}
