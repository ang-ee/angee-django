// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { createRouteHref } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  addons: undefined as { id: string; name: string }[] | undefined,
  query: vi.fn(),
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (...args: unknown[]) => {
    mocks.query(...args);
    return { data: mocks.addons ? { platform_explorer: { addons: mocks.addons } } : undefined };
  },
}));
vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  useRouteHref: () => createRouteHref([
    { name: "platform.addons.record", path: "/platform/addons/$id" },
  ]),
}));

import { PlatformAddonIdentities } from "../documents";
import { AddonLink } from "./AddonLink";

afterEach(cleanup);

describe("AddonLink", () => {
  test("resolves native names through the readable catalogue to canonical record IDs", () => {
    mocks.addons = [{ id: "addon_opaque", name: "example.native" }];
    render(<AddonLink name="example.native">Native addon</AddonLink>);
    expect(screen.getByRole("link", { name: "Native addon" }).getAttribute("href"))
      .toBe("/platform/addons/addon_opaque");
    expect(mocks.query).toHaveBeenCalledWith(PlatformAddonIdentities);
  });

  test("keeps the label readable while pending or absent without inventing a record ID", () => {
    mocks.addons = undefined;
    const view = render(<AddonLink name="example.native">Native addon</AddonLink>);
    expect(screen.getByText("Native addon")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    mocks.addons = [{ id: "addon_other", name: "example.other" }];
    view.rerender(<AddonLink name="example.native">Native addon</AddonLink>);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
