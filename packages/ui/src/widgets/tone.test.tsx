// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { AppRuntimeProvider } from "../runtime/runtime";
import { defaultWidgets } from "./index";
import { toneWidget } from "./tone";

const OPTIONS = [
  { value: "NEUTRAL", label: "Neutral" },
  { value: "WARNING", label: "Warning" },
  { value: "SUCCESS", label: "Success" },
  { value: "INFO", label: "Info" },
  { value: "PURPLE", label: "Purple" },
];

describe("tone widget", () => {
  afterEach(cleanup);

  const Chip = toneWidget.read;

  test("renders each tone in its own colour instead of one status colour", () => {
    render(<>
      {OPTIONS.map((option) => <Chip key={option.value} value={option.value} field={{ options: OPTIONS }} />)}
    </>);
    expect(screen.getByText("Neutral").className).toContain("bg-inset");
    expect(screen.getByText("Warning").className).toContain("bg-warning-soft");
    expect(screen.getByText("Success").className).toContain("bg-success-soft");
    expect(screen.getByText("Info").className).toContain("bg-info-soft");
    expect(screen.getByText("Purple").className).toContain("bg-purple-soft");
  });

  test("reads a stored lowercase value and falls back to neutral for an unknown one", () => {
    render(<>
      <Chip value="danger" field={{ options: [{ value: "DANGER", label: "Danger" }] }} />
      <Chip value="sepia" />
    </>);
    expect(screen.getByText("Danger").className).toContain("bg-danger-soft");
    expect(screen.getByText("sepia").className).toContain("bg-inset");
  });

  test("the picker shows its current value as a chip in its tone", () => {
    const Edit = toneWidget.edit;
    render(<Edit value="SUCCESS" field={{ label: "Tone", options: OPTIONS }} />);
    const trigger = screen.getByRole("combobox", { name: "Tone" });
    expect(trigger.querySelector(".bg-success-soft")?.textContent).toBe("Success");
  });

  test("is registered as a default widget", () => {
    expect(defaultWidgets.tone).toBe(toneWidget);
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><Chip value="BRAND" /></AppRuntimeProvider>);
    expect(screen.getByText("BRAND").className).toContain("bg-brand-soft");
  });
});
