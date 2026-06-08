// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test } from "vitest";

import { Explorer } from "./Explorer";

beforeAll(() => {
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  });
});
afterEach(cleanup);

describe("Explorer", () => {
  test("renders a bare content frame when no side panes are given", () => {
    render(
      <Explorer>
        <div>content</div>
      </Explorer>,
    );
    expect(screen.getByText("content")).toBeTruthy();
  });

  test("renders navigator, content and aside when supplied", () => {
    render(
      <Explorer navigator={<nav>tree</nav>} aside={<aside>preview</aside>}>
        <div>files</div>
      </Explorer>,
    );
    expect(screen.getByText("tree")).toBeTruthy();
    expect(screen.getByText("files")).toBeTruthy();
    expect(screen.getByText("preview")).toBeTruthy();
  });
});
