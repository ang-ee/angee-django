// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { dateWidget } from "./date";
import { datetimeWidget } from "./datetime";

afterEach(cleanup);

test("date widgets keep full record labels and short list cells with full hover text", () => {
  const year = new Date().getFullYear();
  const value = `${year}-09-28`;
  const Read = dateWidget.read;
  const Cell = dateWidget.cell;
  render(<><Read value={value} /><Cell value={value} /><Cell value={null} /></>);
  expect(screen.getByText(`Sep 28, ${year}`)).toBeTruthy();
  expect(screen.getByText("Sep 28").getAttribute("title")).toBe(`Sep 28, ${year}`);
  expect(screen.getByText("—")).toBeTruthy();
});

test("datetime list cells omit the time while record reads keep it", () => {
  const year = new Date().getFullYear();
  const value = `${year}-09-28T09:16:00`;
  const Read = datetimeWidget.read;
  const Cell = datetimeWidget.cell;
  render(<><Read value={value} /><Cell value={value} /></>);
  expect(screen.getByText(`Sep 28, ${year}, 9:16 AM`)).toBeTruthy();
  expect(screen.getByText("Sep 28").getAttribute("title")).toBe(`Sep 28, ${year}, 9:16 AM`);
});
