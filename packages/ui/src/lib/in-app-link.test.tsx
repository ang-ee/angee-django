// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"navigation":{"disableMainFrameNavigation":true,"disableChildPageNavigation":true}}}
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import type { AnchorHTMLAttributes, ReactElement } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { SectionNav } from "../page/SectionNav";
import { MetricTile } from "../fragments/MetricStrip";
import { NavLink } from "../ui/nav-link";
import { TextLink } from "../ui/text-link";
import { GalleryView } from "../views/GalleryView";
import { InAppLinkProvider, useHrefLinkOptions, useInAppLinkClick } from "./in-app-link";

afterEach(cleanup);

function Anchor({ href = "/records/7?view=all#details", onClick, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>): ReactElement {
  const click = useInAppLinkClick(href, onClick);
  return <a {...props} href={href} onClick={click}>Follow</a>;
}

test("the owner calls the user handler before following an internal href", () => {
  const navigate = vi.fn();
  const onClick = vi.fn(() => expect(navigate).not.toHaveBeenCalled());
  render(<InAppLinkProvider navigate={navigate}><Anchor onClick={onClick} target="_self" /></InAppLinkProvider>);
  expect(fireEvent.click(screen.getByRole("link"))).toBe(false);
  expect(onClick).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7?view=all#details");
});

test.each([
  { button: 1 }, { button: 2 }, { altKey: true }, { ctrlKey: true }, { metaKey: true }, { shiftKey: true },
])("modified/non-left activation remains native (%j)", (event) => {
  const navigate = vi.fn();
  render(<InAppLinkProvider navigate={navigate}><Anchor /></InAppLinkProvider>);
  expect(fireEvent.click(screen.getByRole("link"), event)).toBe(true);
  expect(navigate).not.toHaveBeenCalled();
});

test.each([
  { target: "_blank" }, { target: "frame" }, { download: "" }, { download: "report.csv" },
  { href: "https://example.test/records" }, { href: "//example.test/records" },
  { href: "mailto:team@example.test" }, { href: "tel:+1234" }, { href: "#details" }, { href: "records/7" },
])("native destinations and attributes are not intercepted (%j)", (props) => {
  const navigate = vi.fn();
  render(<InAppLinkProvider navigate={navigate}><Anchor {...props} /></InAppLinkProvider>);
  expect(fireEvent.click(screen.getByRole("link"))).toBe(true);
  expect(navigate).not.toHaveBeenCalled();
});

test("a caller can prevent navigation, and disabled primitives do not follow", () => {
  const navigate = vi.fn();
  render(<InAppLinkProvider navigate={navigate}>
    <Anchor onClick={(event) => event.preventDefault()} />
    <TextLink href="/records/7" disabled>Disabled</TextLink>
    <NavLink href="/records/7" disabled>Disabled nav</NavLink>
  </InAppLinkProvider>);
  for (const link of screen.getAllByRole("link")) expect(fireEvent.click(link)).toBe(false);
  expect(navigate).not.toHaveBeenCalled();
});

const primitives = [
  ["TextLink", <TextLink href="/records/7?view=all">Follow</TextLink>],
  ["SectionNav", <SectionNav items={[{ id: "records", label: "Records", href: "/records/7?view=all" }]} />],
  ["NavLink", <NavLink href="/records/7?view=all">Follow</NavLink>],
  ["MetricTile", <MetricTile href="/records/7?view=all" label="Records" value={7} />],
  ["GalleryView", <GalleryView rows={[{ id: "7", title: "Record" }]} cardHref={() => "/records/7?view=all"} />],
] as const;

test.each(primitives)("%s follows by default under a provider", (_name, primitive) => {
  const navigate = vi.fn();
  render(<InAppLinkProvider navigate={navigate}>{primitive}</InAppLinkProvider>);
  const link = screen.getByRole("link");
  expect(link.getAttribute("href")).toBe("/records/7?view=all");
  expect(fireEvent.click(link)).toBe(false);
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7?view=all");
});

test.each(primitives)("%s stays native without a provider or router", (_name, primitive) => {
  render(primitive);
  expect(fireEvent.click(screen.getByRole("link"))).toBe(true);
});

test("gallery links retain their record-opening callback on plain clicks only", () => {
  const navigate = vi.fn();
  const open = vi.fn();
  const row = { id: "7", title: "Record" };
  render(<InAppLinkProvider navigate={navigate}><GalleryView rows={[row]} cardHref={() => "/records/7"} onCardClick={open} /></InAppLinkProvider>);
  const link = screen.getByRole("link");
  fireEvent.click(link, { metaKey: true });
  expect(open).not.toHaveBeenCalled();
  fireEvent.click(link);
  expect(open).toHaveBeenCalledExactlyOnceWith(row);
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7");
});

test("chrome href conversion uses the host search parser, route validation, matching and hash", async () => {
  const parseSearch = vi.fn((search: string) => Object.fromEntries(new URLSearchParams(search)));
  function ChromeLink() {
    return <Link {...useHrefLinkOptions("/records?preset=all&page=2#details")}>Records</Link>;
  }
  const root = createRootRoute({ component: ChromeLink });
  const validateSearch = vi.fn((search: Record<string, unknown>) => search);
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/home" }),
      createRoute({ getParentRoute: () => root, path: "/records", validateSearch }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/home"] }), parseSearch,
    stringifySearch: (search) => { const query = new URLSearchParams(search).toString(); return query ? `?${query}` : ""; },
  });
  render(<RouterProvider router={router} />);
  const link = await screen.findByRole("link", { name: "Records" });
  expect(link.getAttribute("href")).toBe("/records?preset=all&page=2#details");
  expect(fireEvent.click(link)).toBe(false);
  await waitFor(() => expect(router.state.location.pathname).toBe("/records"));
  expect(router.state.location.search).toEqual({ preset: "all", page: "2" });
  expect(router.state.location.hash).toBe("details");
  expect(router.state.matches.at(-1)?.routeId).toBe("/records");
  expect(validateSearch).toHaveBeenCalledWith({ preset: "all", page: "2" });
  await waitFor(() => expect(link.getAttribute("data-status")).toBe("active"));
});

test("slotted anchor hrefs use the owner and metric native attributes remain native", () => {
  const navigate = vi.fn();
  render(<InAppLinkProvider navigate={navigate}>
    <TextLink asChild><a href="/records/7">Slotted</a></TextLink>
    <MetricTile href="/records/7" label="Download" value={7} download="records.csv" />
    <MetricTile href="/records/7" label="New tab" value={7} target="_blank" />
  </InAppLinkProvider>);
  expect(fireEvent.click(screen.getByRole("link", { name: "Slotted" }))).toBe(false);
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7");
  expect(fireEvent.click(screen.getByRole("link", { name: /Download/ }))).toBe(true);
  expect(fireEvent.click(screen.getByRole("link", { name: /New tab/ }))).toBe(true);
  expect(navigate).toHaveBeenCalledOnce();
});
