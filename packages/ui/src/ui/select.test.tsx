// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Select } from "./select";

const OPTIONS = [{ value: "a", label: "Alpha" }];

test("a loading select keeps its name, is busy and disabled, and shows a skeleton instead of copy", () => {
  render(<Select aria-label="Add reviewer" placeholder="Add a reviewer" options={OPTIONS} loading />);

  const trigger = screen.getByRole("combobox", { name: "Add reviewer" });
  expect(trigger.getAttribute("aria-busy")).toBe("true");
  expect(trigger.hasAttribute("disabled") || trigger.getAttribute("aria-disabled") === "true").toBe(true);
  expect(screen.queryByText("Add a reviewer")).toBeNull();
  expect(trigger.querySelector("[aria-hidden='true'].rounded-full")).toBeTruthy();
});

test("a settled select shows its placeholder and is not busy", () => {
  render(<Select aria-label="Add reviewer" placeholder="Add a reviewer" options={OPTIONS} />);

  const trigger = screen.getByRole("combobox", { name: "Add reviewer" });
  expect(trigger.getAttribute("aria-busy")).toBeNull();
  expect(screen.getByText("Add a reviewer")).toBeTruthy();
});
