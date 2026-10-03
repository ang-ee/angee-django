// @vitest-environment happy-dom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { StrictMode } from "react";

import { AppRuntimeProvider } from "../runtime";
import { Breadcrumb, BreadcrumbLabelProvider, useBreadcrumbLeafLabel, useBreadcrumbItems } from "./Breadcrumb";
import { DocumentTitle } from "./DocumentTitle";

const trail = vi.hoisted(() => ({ items: [] as { label: string; href?: string }[] }));
vi.mock("@refinedev/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@refinedev/core")>()),
  useBreadcrumb: () => ({ breadcrumbs: trail.items }),
}));

beforeEach(() => { document.title = "Host title"; trail.items = [{ label: "Notes" }]; });
afterEach(() => { cleanup(); document.title = ""; });

function Leaf({ label }: { label: string }) {
  useBreadcrumbLeafLabel(label);
  return null;
}

function ItemsProbe() {
  const items = useBreadcrumbItems();
  return <output aria-label="Trail">{JSON.stringify(items)}</output>;
}

describe("DocumentTitle and breadcrumb identity", () => {
  test("uses the displayed leaf and runtime brand, updates it, and restores the mount title", async () => {
    const page = (label: string) => <AppRuntimeProvider runtime={{ brand: { name: "Notebook", mark: "book" } }}>
      <BreadcrumbLabelProvider><DocumentTitle /><Breadcrumb /><Leaf label={label} /></BreadcrumbLabelProvider>
    </AppRuntimeProvider>;
    const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/notes/1"] }) });
    const mounted = render(<RouterContextProvider router={router}>{page("First note")}</RouterContextProvider>);
    await waitFor(() => expect(document.title).toBe("First note · Notebook"));
    expect(screen.getByText("First note").getAttribute("aria-current")).toBe("page");
    mounted.rerender(<RouterContextProvider router={router}>{page("Renamed note")}</RouterContextProvider>);
    await waitFor(() => expect(document.title).toBe("Renamed note · Notebook"));
    expect(screen.queryByText("First note")).toBeNull();
    mounted.unmount();
    expect(document.title).toBe("Host title");
  });

  test("uses the title at mount as the unbranded base without accumulating previous leaves", () => {
    const mounted = render(<DocumentTitle />);
    expect(document.title).toBe("Notes · Host title");
    trail.items = [{ label: "Archive" }];
    mounted.rerender(<DocumentTitle />);
    expect(document.title).toBe("Archive · Host title");
    mounted.unmount();
    expect(document.title).toBe("Host title");
  });

  test("does not append a separator when the base title is empty", () => {
    document.title = "";
    const mounted = render(<DocumentTitle />);
    expect(document.title).toBe("Notes");
    mounted.unmount();
    expect(document.title).toBe("");
  });

  test("uses only the brand when there is no breadcrumb leaf", () => {
    trail.items = [];
    render(<AppRuntimeProvider runtime={{ brand: { name: "Notebook", mark: "book" } }}><DocumentTitle /></AppRuntimeProvider>);
    expect(document.title).toBe("Notebook");
  });

  test("shares breadcrumb deduplication and the route's leaf override", async () => {
    trail.items = [{ label: "Notes", href: "/notes" }, { label: "Notes", href: "/notes" }, { label: "Show" }];
    render(<BreadcrumbLabelProvider><DocumentTitle /><ItemsProbe /><Leaf label="First note" /></BreadcrumbLabelProvider>);
    await waitFor(() => expect(document.title).toBe("First note · Host title"));
    expect(JSON.parse(screen.getByLabelText("Trail").textContent ?? "[]")).toEqual([
      { label: "Notes", href: "/notes" }, { label: "First note" },
    ]);
  });

  test("restores the still-mounted parent's title when a nested title unmounts", () => {
    const page = (nested: boolean) => <AppRuntimeProvider runtime={{ brand: { name: "Notebook", mark: "book" } }}>
      <DocumentTitle />
      <section>{nested ? <DocumentTitle /> : null}</section>
    </AppRuntimeProvider>;
    const mounted = render(page(true));
    expect(document.title).toBe("Notes · Notebook");
    mounted.rerender(page(false));
    expect(document.title).toBe("Notes · Notebook");
    mounted.unmount();
    expect(document.title).toBe("Host title");
  });

  test("retains publisher order during updates and restores the remaining publisher's current title", () => {
    const page = (parent: boolean, nested: boolean, name: string) => <AppRuntimeProvider runtime={{ brand: { name, mark: "book" } }}>
      {parent ? <DocumentTitle /> : null}
      <section><AppRuntimeProvider runtime={{ brand: { name: "Archive", mark: "book" } }}>
        {nested ? <DocumentTitle /> : null}
      </AppRuntimeProvider></section>
    </AppRuntimeProvider>;
    const mounted = render(page(true, true, "Notebook"));
    expect(document.title).toBe("Notes · Archive");
    mounted.rerender(page(true, true, "Renamed notebook"));
    expect(document.title).toBe("Notes · Archive");
    mounted.rerender(page(true, false, "Renamed notebook"));
    expect(document.title).toBe("Notes · Renamed notebook");
    mounted.rerender(page(true, true, "Renamed notebook"));
    mounted.rerender(page(false, true, "Renamed notebook"));
    expect(document.title).toBe("Notes · Archive");
    mounted.unmount();
    expect(document.title).toBe("Host title");
  });

  test.each([false, true])("restores the host when all overlapping titles unmount (StrictMode=%s)", (strict) => {
    const page = <AppRuntimeProvider runtime={{ brand: { name: "Notebook", mark: "book" } }}>
      <DocumentTitle /><section><DocumentTitle /></section>
    </AppRuntimeProvider>;
    const mounted = render(strict ? <StrictMode>{page}</StrictMode> : page);
    expect(document.title).toBe("Notes · Notebook");
    mounted.unmount();
    expect(document.title).toBe("Host title");
  });
});
