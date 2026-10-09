export type NumberInput = number | bigint | string | null | undefined;

type NumberFormatOptions = Intl.NumberFormatOptions & { locale?: string };

function numericValue(value: NumberInput): number | bigint | null {
  if (typeof value === "bigint") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (value == null || value.trim() === "") return null;
  if (/^-?\d+$/.test(value)) return BigInt(value);
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function intlNumberFormatter(options?: NumberFormatOptions): Intl.NumberFormat {
  const { locale, ...intlOptions } = options ?? {};
  return new Intl.NumberFormat(locale, intlOptions);
}

/** Build one formatter for repeated values while retaining the shared input rules. */
export function numberFormatter(
  options?: NumberFormatOptions,
): (value: NumberInput) => string {
  const formatter = intlNumberFormatter(options);
  return (value) => {
    const numeric = numericValue(value);
    return numeric === null ? "" : formatter.format(numeric);
  };
}

/** Format a number-like value with native Intl options and no house defaults. */
export function formatNumber(
  value: NumberInput,
  options?: NumberFormatOptions,
): string {
  const numeric = numericValue(value);
  return numeric === null ? "" : intlNumberFormatter(options).format(numeric);
}
