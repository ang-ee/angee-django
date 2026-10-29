import type { ReactElement } from "react";

import {
  formatNumber,
  Input,
  rowValueAtPath,
  textRoleVariants,
  widgetLabel,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

/**
 * A money amount is wired as an exact Decimal string (the F1 Decimal-correct
 * scalar), so the value the widget reads and writes is a string; a plain number
 * is accepted for robustness.
 */
type MoneyWidgetValue = string | number | null;

/**
 * Resolve the ISO-4217 code that denominates the amount from the row, following
 * the MoneyField's `currencyField` path (defaulting to the sibling `"currency"`).
 * Returns undefined when the currency was not selected into the row, so the
 * caller falls back to a currency-neutral format rather than guessing.
 */
function resolveCurrencyCode(row: unknown, currencyField: string | undefined): string | undefined {
  const node = row && typeof row === "object"
    ? rowValueAtPath(row as Record<string, unknown>, currencyField ?? "currency")
    : undefined;
  if (node && typeof node === "object") {
    const code = (node as { code?: unknown }).code;
    if (typeof code === "string" && code.length > 0) return code;
  }
  // A path that resolves straight to a 3-letter code string is honoured too.
  if (typeof node === "string" && node.length === 3) return node;
  return undefined;
}

/**
 * Format an amount for display. With a known currency, `Intl.NumberFormat`'s
 * `currency` style renders the symbol and the currency's own minor-unit digits
 * (JPY 0, BHD 3) from CLDR; without one it falls back to a currency-neutral
 * decimal. Decimal text reaches the shared formatter unchanged so native Intl
 * preserves its exact decimal precision.
 */
export interface FormatMoneyOptions {
  /** A presentation precision that overrides the currency's own minor-unit digits (a company's display places). */
  fractionDigits?: number;
  /** Intl sign display: `"never"` reads an owed amount unsigned whichever side of the ledger holds it. */
  signDisplay?: Intl.NumberFormatOptions["signDisplay"];
}

export function formatMoney(value: MoneyWidgetValue | undefined, code: string | undefined, options: FormatMoneyOptions = {}): string {
  if (value == null || value === "") return "";
  const precision = options.fractionDigits === undefined
    ? {}
    : { minimumFractionDigits: options.fractionDigits, maximumFractionDigits: options.fractionDigits };
  const sign = options.signDisplay === undefined ? {} : { signDisplay: options.signDisplay };
  if (code) {
    try {
      return formatNumber(value, { style: "currency", currency: code, ...precision, ...sign });
    } catch {
      // Unknown/blank code — fall through to the neutral format.
    }
  }
  // No resolved currency (its code was not selected into the row): quantize to a
  // neutral two fraction digits rather than exposing the stored Decimal's full
  // scale (e.g. a 6dp `24.900000`). The currency-styled path above already renders
  // each currency's own minor-unit digits from CLDR when the code is known.
  return formatNumber(value, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    ...precision,
    ...sign,
  });
}

/** The row's presentation precision for a money field, when the field names where it lives. */
function resolveFractionDigits(row: Record<string, unknown> | undefined, path: string | undefined): number | undefined {
  if (!row || !path) return undefined;
  const value = path.split(".").reduce<unknown>((current, key) => (current && typeof current === "object" ? (current as Record<string, unknown>)[key] : undefined), row);
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function MoneyRead({ value, row, field }: WidgetRenderProps<MoneyWidgetValue>): ReactElement {
  const code = resolveCurrencyCode(row, field?.currencyField);
  const fractionDigits = resolveFractionDigits(row as Record<string, unknown> | undefined, field?.fractionDigitsField);
  return <span className={textRoleVariants({ role: "value" })}>{formatMoney(value, code, { fractionDigits })}</span>;
}

function MoneyEdit({
  value,
  onChange,
  field,
  row,
  readOnly,
  controlRef,
}: WidgetRenderProps<MoneyWidgetValue>): ReactElement {
  const code = resolveCurrencyCode(row, field?.currencyField);
  const stored = typeof value === "string" ? /^([-+]?\d+)\.(\d{6})$/.exec(value) : null;
  const integer = stored?.[1] ?? "";
  const fraction = stored?.[2] ?? "";
  const currencyDigits = (() => {
    if (!code) return 2;
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).resolvedOptions().maximumFractionDigits ?? 2; }
    catch { return 2; }
  })();
  const displayValue = stored && /^0*$/.test(fraction.slice(currencyDigits))
    ? `${integer}${currencyDigits ? `.${fraction.slice(0, currencyDigits)}` : ""}`
    : value == null ? "" : String(value);
  return (
    <Input
      {...field?.controlProps}
      ref={controlRef}
      value={displayValue}
      readOnly={readOnly}
      inputMode="decimal"
      aria-label={widgetLabel(field, "Amount")}
      className="text-right tabular-nums"
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
