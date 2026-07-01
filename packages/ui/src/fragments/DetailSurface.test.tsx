// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { DetailSection, DetailSurface } from "./DetailSurface";

afterEach(() => cleanup());

describe("DetailSurface", () => {
  test("renders the shared loading state before detail chrome", () => {
    render(
      <DetailSurface loading loadingMessage="Loading service" title="Ignored">
        <span>Hidden body</span>
      </DetailSurface>,
    );

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText("Loading service")).toBeTruthy();
    expect(screen.queryByText("Ignored")).toBeNull();
    expect(screen.queryByText("Hidden body")).toBeNull();
  });

  test("renders the shared empty state before detail chrome", () => {
    render(
      <DetailSurface
        empty={{ icon: "grid", title: "Missing model", description: "notes.Note" }}
        title="Ignored"
      >
        <span>Hidden body</span>
      </DetailSurface>,
    );

    expect(screen.getByRole("heading", { name: "Missing model" })).toBeTruthy();
    expect(screen.getByText("notes.Note")).toBeTruthy();
    expect(screen.queryByText("Ignored")).toBeNull();
    expect(screen.queryByText("Hidden body")).toBeNull();
  });

  test("renders header slots, metrics, sections, and children", () => {
    render(
      <DetailSurface
        title="Worker"
        meta={<span>running</span>}
        actions={<button type="button">Restart</button>}
        metrics={[
          { label: "Fields", value: 12 },
          { label: "Relations", value: 3 },
        ]}
      >
        <DetailSection
          title="Overview"
          rows={[
            ["Runtime", "node"],
            ["Endpoint", "http://localhost:8000"],
          ]}
        />
        <section>Log tail</section>
      </DetailSurface>,
    );

    expect(screen.getByRole("heading", { name: "Worker" })).toBeTruthy();
    expect(screen.getByText("running")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Restart" })).toBeTruthy();
    expect(screen.getByText("Fields")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Overview" })).toBeTruthy();
    expect(screen.getByText("Runtime")).toBeTruthy();
    expect(screen.getByText("node")).toBeTruthy();
    expect(screen.getByText("Log tail")).toBeTruthy();
  });

  test("DetailSection can render custom children instead of metadata rows", () => {
    render(
      <DetailSection title="Related models">
        <a href="/models">notes.Note</a>
      </DetailSection>,
    );

    expect(screen.getByRole("heading", { name: "Related models" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "notes.Note" })).toBeTruthy();
  });
});
