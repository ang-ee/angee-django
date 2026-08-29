// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { jsonWidget } from "./json";

describe("json widget", () => {
  afterEach(cleanup);

  test("read renders an object as a tree of keys and values", () => {
    const Read = jsonWidget.read;
    render(<Read value={{ github_org: "acme", retries: 7 }} />);

    expect(screen.getByText(/github_org/)).toBeTruthy();
    expect(screen.getByText(/acme/)).toBeTruthy();
    expect(screen.getByText(/7/)).toBeTruthy();
  });

  test("read falls back to compact form for a scalar value", () => {
    const Read = jsonWidget.read;
    render(<Read value={"plain"} />);

    expect(screen.getByText('"plain"')).toBeTruthy();
  });

  test("edit mounts a labelled JSON editor", () => {
    const Edit = jsonWidget.edit;
    render(<Edit value={{}} field={{ label: "Config" }} />);

    expect(screen.getByLabelText("Config")).toBeTruthy();
  });

  test("cell renders compact json", () => {
    const Cell = jsonWidget.cell;
    render(<Cell value={{ a: 1 }} />);

    expect(screen.getByText('{"a":1}')).toBeTruthy();
  });
});
