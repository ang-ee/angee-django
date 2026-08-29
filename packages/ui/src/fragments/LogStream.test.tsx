// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { LogStream } from "./LogStream";

afterEach(() => cleanup());

describe("LogStream", () => {
  test("renders each line in order", () => {
    render(<LogStream lines={["first line", "second line", "third line"]} />);
    expect(screen.getByText("first line")).toBeTruthy();
    expect(screen.getByText("second line")).toBeTruthy();
    expect(screen.getByText("third line")).toBeTruthy();
  });

  test("shows the empty message when there are no lines", () => {
    render(<LogStream lines={[]} emptyContent="Waiting for output…" />);
    expect(screen.getByText("Waiting for output…")).toBeTruthy();
  });
});
