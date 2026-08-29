// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { SessionRail, SessionRailItem } from "./index";

describe("SessionRail", () => {
  afterEach(() => {
    cleanup();
  });

  test("labels the nav landmark and renders the action over a ul > li list", () => {
    render(
      <SessionRail label="Running agents" action={<button type="button">New</button>}>
        <SessionRailItem>Scout</SessionRailItem>
      </SessionRail>,
    );

    expect(screen.getByRole("navigation", { name: "Running agents" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "New" })).toBeTruthy();
    // nav > ul > li (the row anchor lives inside the li).
    const list = screen.getByRole("list");
    const item = screen.getByRole("listitem");
    expect(list.contains(item)).toBe(true);
    expect(item.querySelector("a")).toBeTruthy();
  });

  test("marks the active row aria-current=page + data-active and renders status + handle", () => {
    render(
      <SessionRail label="Running agents">
        <SessionRailItem active status={<span data-testid="dot" />} handle="claude-opus">
          Scout
        </SessionRailItem>
      </SessionRail>,
    );

    const anchor = screen.getByRole("listitem").querySelector("a");
    expect(anchor?.getAttribute("aria-current")).toBe("page");
    expect(anchor?.getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("dot")).toBeTruthy();
    expect(screen.getByText("claude-opus")).toBeTruthy();
    expect(screen.getByText("Scout")).toBeTruthy();
  });

  test("renders the row through the supplied Link element via render", () => {
    render(
      <SessionRail label="Running agents">
        <SessionRailItem render={<a href="/agents/sessions/a1" data-testid="row-link" />}>
          Scout
        </SessionRailItem>
      </SessionRail>,
    );

    const link = screen.getByTestId("row-link");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/agents/sessions/a1");
    expect(link.textContent).toContain("Scout");
  });
});
