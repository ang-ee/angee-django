import { useMemo, type ReactElement } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { useMenus } from "@angee/sdk";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import {
  type ChromeMenuItem,
  type ChromeMenuNode,
  MenuTree,
} from "./menu-tree";

/**
 * The active app's sections, plus whether the app opts into a left settings-style
 * sub-nav. An app declares `sidebar: true` on its root menu item to render its
 * sections in the sidebar *as well as* the top bar — the two surfaces are
 * independent, so the top bar always shows the sections (as links or dropdowns)
 * and the sidebar is the opt-in extra. Opt-in is one flag, so any addon turns the
 * sidebar on or off without touching the chrome. `ConsoleShell` reads `show` to
 * pick its grid; `ConsoleSubNav` reads the rest to render. Independent of `group`
 * (which only places the app in the rail's domain/platform zone).
 */
export function useConsoleSubNav(): {
  show: boolean;
  sections: readonly ChromeMenuNode[];
  pathname: string;
} {
  const menus = useMenus() as readonly ChromeMenuItem[];
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const tree = useMemo(() => MenuTree.from(menus), [menus]);
  const activeRoot = tree.activeAppRoot(pathname);
  const sections = tree.appSectionItems(pathname);
  const show = activeRoot?.sidebar === true && sections.length > 0;
  return { show, sections, pathname };
}

export function ConsoleSubNav({
  className,
}: {
  className?: string;
}): ReactElement | null {
  const t = useBaseT();
  const { show, sections, pathname } = useConsoleSubNav();
  if (!show) return null;
  return (
    <nav
      aria-label={t("chrome.sectionNav")}
      className={cn(
        "area-sidebar z-topbar flex min-h-0 w-rail-sub flex-col gap-0.5 overflow-y-auto border-r border-border-subtle bg-sheet px-2 py-3",
        className,
      )}
    >
      {sections.map((section) =>
        section.targetedChildren.length ? (
          <SubNavGroup key={section.id} group={section} pathname={pathname} />
        ) : (
          <SubNavLink key={section.id} item={section} pathname={pathname} />
        ),
      )}
    </nav>
  );
}

function SubNavGroup({
  group,
  pathname,
}: {
  group: ChromeMenuNode;
  pathname: string;
}): ReactElement {
  return (
    <div className="mt-3 first:mt-0">
      <div className="px-2 pb-1 text-2xs font-semibold uppercase tracking-wide text-fg-muted">
        {group.displayLabel}
      </div>
      {group.targetedChildren.map((child) => (
        <SubNavLink key={child.id} item={child} pathname={pathname} />
      ))}
    </div>
  );
}

function SubNavLink({
  item,
  pathname,
}: {
  item: ChromeMenuNode;
  pathname: string;
}): ReactElement | null {
  const target = item.target;
  if (!target) return null;
  const active = item.isActive(pathname);
  return (
    <Link
      to={target}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-8 items-center rounded-md px-2 text-13 text-fg-2 no-underline outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring",
        active && "bg-brand-soft font-medium text-brand-soft-text hover:bg-brand-soft",
      )}
    >
      <span className="truncate">{item.displayLabel}</span>
    </Link>
  );
}
