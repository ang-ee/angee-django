// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRouter,
} from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import {
  Breadcrumb,
  BreadcrumbLabelProvider,
  useBreadcrumbLeafLabel,
  useBreadcrumbCollectionLink,
} from "./Breadcrumb";
import type { ChromeMenuItem } from "./menu-tree";
import { ChromePlaceProvider } from "./refine-menu";

const refineMocks = vi.hoisted(() => ({
  breadcrumbs: [] as { label: string; href?: string }[],
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useBreadcrumb: () => ({ breadcrumbs: refineMocks.breadcrumbs }),
  };
});

afterEach(() => {
  cleanup();
  refineMocks.breadcrumbs = [];
});

describe("Breadcrumb", () => {
  test("renders the refine breadcrumb trail", async () => {
    refineMocks.breadcrumbs = [
      { label: "Notes", href: "/notes" },
      { label: "First note" },
    ];

    renderBreadcrumb();

    const breadcrumb = await screen.findByRole("navigation", {
      name: "Breadcrumb",
    });
    expect(within(breadcrumb).getByText("Notes").closest("a")?.getAttribute("href"))
      .toBe("/notes");
    expect(within(breadcrumb).getByText("First note").getAttribute("aria-current"))
      .toBe("page");
  });

  test("only the owning collection crumb uses its prepared return URL", async () => {
    refineMocks.breadcrumbs = [
      { label: "Home", href: "/" },
      { label: "Files", href: "/storage" },
      { label: "Show" },
    ];
    renderBreadcrumb({ collection: { to: "/storage", href: "/storage?folder=project&group=extension&pageSize=50" } });
    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByRole("link", { name: "Home" }).getAttribute("href")).toBe("/");
    await waitFor(() => {
      expect(within(breadcrumb).getByRole("link", { name: "Files" }).getAttribute("href"))
        .toBe("/storage?folder=project&group=extension&pageSize=50");
    });
  });

  test("uses the route-provided leaf label for the current crumb", async () => {
    refineMocks.breadcrumbs = [
      { label: "Files", href: "/storage" },
      { label: "Show" },
    ];

    renderBreadcrumb({ leafLabel: "alex-profile.jpg" });

    const breadcrumb = await screen.findByRole("navigation", {
      name: "Breadcrumb",
    });
    expect(within(breadcrumb).getByText("Files").closest("a")?.getAttribute("href"))
      .toBe("/storage");
    expect((await within(breadcrumb).findByText("alex-profile.jpg")).getAttribute("aria-current"))
      .toBe("page");
    expect(within(breadcrumb).queryByText("Show")).toBeNull();
  });

  test("collapses adjacent menu groups with the same label to the deepest route", async () => {
    refineMocks.breadcrumbs = [
      { label: "Integrations", href: "/integrate" },
      { label: "Integrations", href: "/integrate" },
      { label: "Integrations", href: "/integrate" },
      { label: "Show" },
    ];

    renderBreadcrumb({ leafLabel: "Local checkout" });

    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getAllByText("Integrations")).toHaveLength(1);
    expect(within(breadcrumb).getByRole("link", { name: "Integrations" }).getAttribute("href"))
      .toBe("/integrate");
    expect((await within(breadcrumb).findByText("Local checkout")).getAttribute("aria-current"))
      .toBe("page");
  });

  test("keeps equal adjacent labels when they navigate to different places", async () => {
    refineMocks.breadcrumbs = [
      { label: "Records", href: "/records" },
      { label: "Records", href: "/records/nested" },
      { label: "Records" },
    ];

    renderBreadcrumb();

    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getAllByText("Records")).toHaveLength(3);
    expect(within(breadcrumb).getAllByRole("link").map((link) => link.getAttribute("href")))
      .toEqual(["/records", "/records/nested"]);
    expect(within(breadcrumb).getAllByText("Records").at(-1)?.getAttribute("aria-current"))
      .toBe("page");
  });
});

describe("Breadcrumb app lead", () => {
  const desk: readonly ChromeMenuItem[] = [{ id: "desk", label: "Desk", to: "/desk", children: [
    { id: "desk.notes", label: "Notes", to: "/desk/notes" },
  ] }];

  test("leads with the selected app when the trail does not name it", async () => {
    refineMocks.breadcrumbs = [{ label: "Notes" }];
    renderBreadcrumb({ menus: desk, path: "/desk/notes" });
    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb.textContent).toBe("Desk/Notes");
    expect(within(breadcrumb).getByRole("link", { name: "Desk" }).getAttribute("href")).toBe("/desk");
  });

  test("does not repeat an app the trail already names", async () => {
    refineMocks.breadcrumbs = [{ label: "Desk", href: "/desk" }, { label: "Notes" }];
    renderBreadcrumb({ menus: desk, path: "/desk/notes" });
    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getAllByText("Desk")).toHaveLength(1);
  });

  test("leads Settings pages with the Settings place", async () => {
    refineMocks.breadcrumbs = [{ label: "Tags", href: "/tags" }, { label: "Urgent" }];
    renderBreadcrumb({ menus: [{ id: "tags", label: "Tags", group: "platform", to: "/tags" }], path: "/tags/urgent" });
    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb.textContent).toBe("Settings/Tags/Urgent");
    expect(within(breadcrumb).getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/tags");
  });
});

function renderBreadcrumb({
  leafLabel,
  collection,
  menus,
  path = "/notes/first",
}: {
  leafLabel?: string;
  collection?: { to: string; href: string };
  menus?: readonly ChromeMenuItem[];
  path?: string;
} = {}): void {
  const trail = (
    <BreadcrumbLabelProvider>
      <Breadcrumb />
      {leafLabel ? <BreadcrumbLeaf label={leafLabel} /> : null}
      {collection ? <CollectionLink {...collection} /> : null}
    </BreadcrumbLabelProvider>
  );
  const rootRoute = createRootRoute({
    component: () => menus
      ? <AppRuntimeProvider runtime={{}}><ChromePlaceProvider menuItems={menus}>{trail}</ChromePlaceProvider></AppRuntimeProvider>
      : trail,
  });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

function BreadcrumbLeaf({ label }: { label: string }): null {
  useBreadcrumbLeafLabel(label);
  return null;
}

function CollectionLink({ to, href }: { to: string; href: string }): null {
  useBreadcrumbCollectionLink(to, href);
  return null;
}
