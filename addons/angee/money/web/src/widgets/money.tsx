import type { ReactElement } from "react";

import {
  TextInput,
  widgetLabel,
  type WidgetDefinition,
  type WidgetField,
  type WidgetRenderProps,
} from "@angee/ui";

/**
 * A money amount is wired as an exact Decimal string (the F1 Decimal-correct
 * scalar), so the value the widget reads and writes is a string; a plain number
 * is accepted for robustness.
 */
type MoneyWidgetValue = string | number | null;

/**
 * The field metadata a MoneyField projects (via the backend classifier): the
 * path to the FK that owns the row's currency, a sibling `"currency"` or a
 * one-hop `"order.currency"`. Widened locally over {@link WidgetField} — the
 * currency path is money's own contract, resolved from resource metadata.
 */
export interface MoneyWidgetField extends WidgetField {
  currencyField?: string;
}

/**
 * Read the value at a dotted path (`"order.currency"`) on a row, tolerating
 * absent segments — the row may not have joined the currency relation.
 */
function readPath(row: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((node, key) => {
    if (node && typeof node === "object") {
      return (node as Record<string, unknown>)[key];
    }
    return undefined;
  }, row);
}

/**
 * Resolve the ISO-4217 code that denominates the amount from the row, following
 * the MoneyField's `currencyField` path (defaulting to the sibling `"currency"`).
 * Returns undefined when the currency was not selected into the row, so the
 * caller falls back to a currency-neutral format rather than guessing.
 */
function resolveCurrencyCode(row: unknown, currencyField: string | undefined): string | undefined {
  const node = readPath(row, currencyField ?? "currency");
  if (node && typeof node === "object") {
    const code = (node as { code?: unknown }).code;
    if (typeof code === "string" && code.length > 0) return code;
  }
  // A path that resolves straight to a 3-letter code string is honoured too.
  if (typeof node === "string" && node.length === 3) return node;
  return undefined;
}

function toAmount(value: MoneyWidgetValue | undefined): number | null {
  if (value == null || value === "") return null;
  const amount = typeof value === "number" ? value : Number(value);
  return Number.isFinite(amount) ? amount : null;
}

/**
 * Format an amount for display. With a known currency, `Intl.NumberFormat`'s
 * `currency` style renders the symbol and the currency's own minor-unit digits
 * (JPY 0, BHD 3) from CLDR; without one it falls back to a currency-neutral
 * decimal. Display coerces the Decimal string to a number for `Intl`; the exact
 * string is preserved by the edit control, which is where precision matters.
 */
function formatMoney(value: MoneyWidgetValue | undefined, code: string | undefined): string {
  const amount = toAmount(value);
  if (amount === null) return "";
  if (code) {
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(amount);
    } catch {
      // Unknown/blank code — fall through to the neutral format.
    }
  }
  // No resolved currency (its code was not selected into the row): quantize to a
  // neutral two fraction digits rather than exposing the stored Decimal's full
  // scale (e.g. a 6dp `24.900000`). The currency-styled path above already renders
  // each currency's own minor-unit digits from CLDR when the code is known.
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

function MoneyRead({ value, row, field }: WidgetRenderProps<MoneyWidgetValue>): ReactElement {
  const code = resolveCurrencyCode(row, (field as MoneyWidgetField | undefined)?.currencyField);
  return <span className="text-13 tabular-nums text-fg">{formatMoney(value, code)}</span>;
}

function MoneyEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<MoneyWidgetValue>): ReactElement {
  return (
    <TextInput
      value={value == null ? "" : String(value)}
      readOnly={readOnly}
      inputMode="decimal"
      aria-label={widgetLabel(field, "Amount")}
      className="tabular-nums"
      onChange={(event) => onChange?.(event.currentTarget.value)}
    />
  );
}

/**
 * The `"money"` widget: renders an amount with its currency (resolved from the
 * row through the MoneyField's `currencyField` path) and edits it as an exact
 * decimal string. Registered against the backend-owned `"money"` widget key by
 * the addon manifest.
 */
export const moneyWidget = {
  edit: MoneyEdit,
  read: MoneyRead,
  cell: MoneyRead,
} satisfies WidgetDefinition<MoneyWidgetValue>;
