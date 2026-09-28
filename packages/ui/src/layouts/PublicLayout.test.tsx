// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider, type AppRuntime } from "../runtime";
import { PublicLayout, type PublicLayoutProps } from "./PublicLayout";

vi.mock("../theme/logo", () => ({ ThemeLogo: () => <svg data-testid="theme-mark" /> }));
afterEach(cleanup);

const branded: Partial<AppRuntime> = {
  brand: { name: "Notebook", mark: "notebook-mark" },
  icons: { "notebook-mark": () => <svg data-testid="runtime-mark" /> },
};

function layout(props: Omit<PublicLayoutProps, "children"> = {}, runtime: Partial<AppRuntime> = branded) {
  return <AppRuntimeProvider runtime={runtime}>
    <PublicLayout {...props}><form aria-label="Public form">Form body</form></PublicLayout>
  </AppRuntimeProvider>;
}

describe("PublicLayout identity", () => {
  test("defaults the corner mark to the runtime glyph", () => {
    render(layout());
    expect(screen.getByTestId("runtime-mark")).toBeTruthy();
    expect(screen.queryByTestId("theme-mark")).toBeNull();
  });

  test("an explicit mark overrides the runtime glyph", () => {
    render(layout({ mark: <span>Explicit mark</span> }));
    expect(screen.getByText("Explicit mark")).toBeTruthy();
    expect(screen.queryByTestId("runtime-mark")).toBeNull();
    expect(screen.queryByTestId("theme-mark")).toBeNull();
  });

  test.each([branded, {}])("null suppresses both brand and theme marks", (runtime) => {
    render(layout({ mark: null }, runtime));
    expect(screen.queryByTestId("runtime-mark")).toBeNull();
    expect(screen.queryByTestId("theme-mark")).toBeNull();
    expect(screen.getByRole("form", { name: "Public form" })).toBeTruthy();
  });

  test("falls back to the theme mark on an unbranded host", () => {
    render(layout({}, { brand: null }));
    expect(screen.getByTestId("theme-mark")).toBeTruthy();
  });

  test("the no-hero card fills its section and uses theme-aware surface tokens", () => {
    render(layout({ hero: null, cardLead: "Lead", brand: "Legacy lead" }));
    const form = screen.getByRole("form", { name: "Public form" });
    expect(form.closest("section")?.classList.contains("w-full")).toBe(true);
    expect(form.parentElement?.classList.contains("bg-sheet")).toBe(true);
    expect(form.parentElement?.classList.contains("border-border-subtle")).toBe(true);
    expect(screen.getByText("Lead")).toBeTruthy();
    expect(screen.queryByText("Legacy lead")).toBeNull();
  });
});
