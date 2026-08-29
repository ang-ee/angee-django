// @vitest-environment happy-dom

import { cleanup, fireEvent, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { moneyWidget, type MoneyWidgetField } from "./money";

// Rendering runs under happy-dom in the environment's default locale (en-US on
// CI, full ICU). The assertions lean on currency-driven facts — the symbol and
// the minor-unit digit count come from the ISO code, not the locale — rather than
// locale-driven grouping separators.

afterEach(cleanup);

describe("moneyWidget.read", () => {
  it("renders the amount with the row's sibling currency", () => {
    const { container } = render(
      createElement(moneyWidget.read, {
        value: "1234.50",
        row: { currency: { code: "USD" } },
      }),
    );
    const text = container.textContent ?? "";
    expect(text).toContain("$");
    expect(text).toMatch(/\.\d{2}\b/); // USD carries two minor-unit digits
  });

  it("resolves a one-hop currency path from field metadata", () => {
    const field: MoneyWidgetField = { currencyField: "order.currency" };
    const { container } = render(
      createElement(moneyWidget.read, {
        value: "1000",
        row: { order: { currency: { code: "EUR" } } },
        field,
      }),
    );
    expect(container.textContent ?? "").toContain("€");
  });

  it("uses the currency's own exponent (JPY has no minor unit)", () => {
    const { container } = render(
      createElement(moneyWidget.read, {
        value: "1235",
        row: { currency: { code: "JPY" } },
      }),
    );
    const text = container.textContent ?? "";
    expect(text).toContain("¥");
    expect(text).not.toMatch(/\.\d/); // zero minor-unit digits — no fractional part
  });

  it("falls back to a currency-neutral format when the currency is absent", () => {
    const { container } = render(
      createElement(moneyWidget.read, { value: "1234.5", row: {} }),
    );
    const text = container.textContent ?? "";
    expect(text).not.toContain("$");
    expect(text).not.toContain("€");
    expect(text).toMatch(/1[.,]?234/); // the amount still renders
  });

  it("renders empty for a null amount", () => {
    const { container } = render(
      createElement(moneyWidget.read, { value: null, row: { currency: { code: "USD" } } }),
    );
    expect((container.textContent ?? "").trim()).toBe("");
  });
});

describe("moneyWidget.edit", () => {
  it("preserves the exact decimal string and emits it unchanged", () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      createElement(moneyWidget.edit!, { value: "1234.567890", onChange }),
    );
    const input = getByRole("textbox") as HTMLInputElement;
    expect(input.value).toBe("1234.567890");
    fireEvent.change(input, { target: { value: "99.99" } });
    expect(onChange).toHaveBeenCalledWith("99.99");
  });
});
