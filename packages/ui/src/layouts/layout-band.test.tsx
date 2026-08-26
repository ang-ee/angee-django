// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test } from "vitest";

import { createLayoutBand } from "./layout-band";

afterEach(() => cleanup());

const band = createLayoutBand("div", "test-band");

function Harness({ children }: { children: React.ReactNode }) {
  const [host, setHost] = React.useState<HTMLElement | null>(null);
  return (
    <band.Provider host={host}>
      <div data-testid="row" ref={setHost} />
      <main data-testid="page">{children}</main>
    </band.Provider>
  );
}

describe("createLayoutBand", () => {
  test("a solitary band portals into the host row", () => {
    render(
      <Harness>
        <band.Band>only</band.Band>
      </Harness>,
    );
    expect(screen.getByTestId("row").textContent).toBe("only");
    expect(screen.getByTestId("page").textContent).not.toContain("only");
  });

  test("two bands on one page both render inline, never stacked in the row", () => {
    render(
      <Harness>
        <section data-testid="first">
          <band.Band>alpha</band.Band>
        </section>
        <section data-testid="second">
          <band.Band>beta</band.Band>
        </section>
      </Harness>,
    );
    expect(screen.getByTestId("row").childNodes.length).toBe(0);
    expect(screen.getByTestId("first").textContent).toContain("alpha");
    expect(screen.getByTestId("second").textContent).toContain("beta");
  });

  test("a band under a nested opt-out provider neither claims nor counts against the page row", () => {
    render(
      <Harness>
        <band.Band>page</band.Band>
        <section data-testid="drawer">
          <band.Provider host={undefined}>
            <band.Band>drawer</band.Band>
          </band.Provider>
        </section>
      </Harness>,
    );
    expect(screen.getByTestId("row").textContent).toBe("page");
    expect(screen.getByTestId("drawer").textContent).toContain("drawer");
  });

  test("with no provider the band renders inline", () => {
    render(<band.Band>bare</band.Band>);
    expect(screen.getByText("bare")).toBeTruthy();
  });
});
