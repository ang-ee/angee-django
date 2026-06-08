// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { Notebook, Tab } from "./Notebook";

afterEach(cleanup);

describe("Notebook", () => {
  test("shows the first tab's panel and switches on tab click", () => {
    render(
      <Notebook>
        <Tab id="profile" label="Profile">
          <p>profile body</p>
        </Tab>
        <Tab id="security" label="Security">
          <p>security body</p>
        </Tab>
      </Notebook>,
    );
    expect(screen.getByText("profile body")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Security" }));
    expect(screen.getByText("security body")).toBeTruthy();
  });

  test("hidden tabs are skipped; controlled value drives the active tab", () => {
    const onValueChange = vi.fn();
    render(
      <Notebook value="b" onValueChange={onValueChange}>
        <Tab id="a" label="A">
          <p>a body</p>
        </Tab>
        <Tab id="hidden" label="Hidden" hidden>
          <p>hidden body</p>
        </Tab>
        <Tab id="b" label="B">
          <p>b body</p>
        </Tab>
      </Notebook>,
    );
    expect(screen.queryByRole("tab", { name: "Hidden" })).toBeNull();
    expect(screen.getByText("b body")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "A" }));
    expect(onValueChange).toHaveBeenCalledWith("a");
  });
});
