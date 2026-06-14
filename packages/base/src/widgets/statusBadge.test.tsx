// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { statusBadgeWidget } from "./statusBadge";

describe("statusBadge widget tone", () => {
  afterEach(() => {
    cleanup();
  });

  const Badge = statusBadgeWidget.read;

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
});
