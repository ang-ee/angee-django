// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { statusBadgeWidget } from "./statusBadge";
import { AppRuntimeProvider } from "../runtime/runtime";

describe("statusBadge widget tone", () => {
  afterEach(() => {
    cleanup();
  });

  const Badge = statusBadgeWidget.read;

  test("keeps contributed vocabulary scoped to its app and honors field overrides", () => {
    render(<>
      <AppRuntimeProvider runtime={{ statusTones: { reviewed: "accent" } }}>
        <Badge value="REVIEWED" field={{ options: [{ value: "REVIEWED", label: "Contributed" }] }} />
        <Badge value="REVIEWED" field={{ tone: { REVIEWED: "danger" }, options: [{ value: "REVIEWED", label: "Overridden" }] }} />
      </AppRuntimeProvider>
      <Badge value="reviewed" field={{ options: [{ value: "reviewed", label: "Other app" }] }} />
    </>);
    expect(screen.getByText("Contributed").className).toContain("bg-accent-soft");
    expect(screen.getByText("Overridden").className).toContain("bg-danger-soft");
    expect(screen.getByText("Other app").className).toContain("bg-brand-soft");
  });

  test("colors known status values via the widget convention", () => {
    render(
      <>
        <Badge
          value="active"
          field={{ options: [{ value: "active", label: "Active" }] }}
        />
        <Badge
          value="draft"
          field={{ options: [{ value: "draft", label: "Draft" }] }}
        />
      </>,
    );
    expect(screen.getByText("Active").className).toContain("bg-success-soft");
    expect(screen.getByText("Draft").className).toContain("bg-warning-soft");
  });

  test("colors integration connection lifecycle values", () => {
    render(
      <>
        <Badge
          value="CONNECTED"
          field={{ options: [{ value: "CONNECTED", label: "Connected" }] }}
        />
        <Badge
          value="DISCONNECTED"
          field={{ options: [{ value: "DISCONNECTED", label: "Disconnected" }] }}
        />
      </>,
    );
    expect(screen.getByText("Connected").className).toContain("bg-success-soft");
    expect(screen.getByText("Disconnected").className).toContain("bg-inset");
  });

  test("the convention lowercases the value (UPPERCASE enum member reads)", () => {
    // The read side serializes the UPPERCASE member name; the convention's
    // lowercase vocabulary must still match it.
    render(
      <Badge
        value="ACTIVE"
        field={{ options: [{ value: "ACTIVE", label: "Active" }] }}
      />,
    );
    expect(screen.getByText("Active").className).toContain("bg-success-soft");
  });

  test("an explicit <Column tone> map overrides the convention", () => {
    render(
      <Badge
        value="active"
        field={{
          options: [{ value: "active", label: "Active" }],
          tone: { active: "danger" },
        }}
      />,
    );
    expect(screen.getByText("Active").className).toContain("bg-danger-soft");
  });

  test("the tone map keys the value exactly as it reads (UPPERCASE enum member)", () => {
    // A StateField column reads the UPPERCASE member name; the `<Column tone>`
    // map keys it the same way (matching cellContent / BoardView's exact lookup).
    render(
      <Badge
        value="ACTIVE"
        field={{
          options: [{ value: "ACTIVE", label: "Active" }],
          tone: { ACTIVE: "info" },
        }}
      />,
    );
    expect(screen.getByText("Active").className).toContain("bg-info-soft");
  });

  test("a value the override map misses still gets the widget convention", () => {
    // Unlike a plain cellContent/BoardView cell (which falls to neutral on a
    // miss), the badge layers its convention over a partial `<Column tone>` map.
    render(
      <Badge
        value="pending"
        field={{
          options: [{ value: "pending", label: "Pending" }],
          tone: { active: "success" },
        }}
      />,
    );
    expect(screen.getByText("Pending").className).toContain("bg-warning-soft");
  });

  test("an unknown value with no override falls back to the brand tone", () => {
    render(
      <Badge
        value="bespoke"
        field={{ options: [{ value: "bespoke", label: "Bespoke" }] }}
      />,
    );
    expect(screen.getByText("Bespoke").className).toContain("bg-brand-soft");
  });

  test("edit renders the shared status select owner", () => {
    const Edit = statusBadgeWidget.edit;

    render(
      <Edit
        value="draft"
        field={{
          options: [
            { value: "draft", label: "Draft" },
            { value: "active", label: "Active" },
          ],
        }}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Status" })).toBeTruthy();
    expect(screen.getByText("Draft")).toBeTruthy();
  });
});
