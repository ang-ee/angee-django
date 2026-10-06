// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { tagInputWidget } from "./tagInput";

afterEach(cleanup);

test("tag input removes a chip or the last tag, and adds typed tags once", () => {
  const Edit = tagInputWidget.edit;
  const onChange = vi.fn();
  render(<Edit value={["triage", " backend ", "triage", ""]} field={{ label: "Labels" }} onChange={onChange} />);
  expect(screen.getAllByRole("button", { name: /^Remove / })).toHaveLength(2);

  fireEvent.click(screen.getByRole("button", { name: "Remove triage" }));
  expect(onChange).toHaveBeenLastCalledWith(["backend"]);
  const input = screen.getByRole("textbox", { name: "Labels" });
  fireEvent.keyDown(input, { key: "Backspace" });
  expect(onChange).toHaveBeenLastCalledWith(["triage"]);
  fireEvent.change(input, { target: { value: "backend, release" } });
  expect(onChange).toHaveBeenLastCalledWith(["triage", "backend", "release"]);
});

test("a read-only tag list shows each distinct tag without remove buttons", () => {
  const Read = tagInputWidget.read;
  render(<Read value={["triage", "triage", "backend"]} />);
  expect(screen.getAllByText("triage")).toHaveLength(1);
  expect(screen.getByText("backend")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
