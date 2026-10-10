import { getHumanLocale } from "./human-locale";

export type NumberInput = number | bigint | string | null | undefined;

type NumberFormatOptions = Intl.NumberFormatOptions & { locale?: string };

const NUMERIC_STRING = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

/** Whether Intl can consume a decimal string without first losing precision. */
export function isNumericString(value: string): boolean {
  return NUMERIC_STRING.test(value.trim());
}

function intlNumberFormatter(options?: NumberFormatOptions): Intl.NumberFormat {
  const { locale, ...intlOptions } = options ?? {};
  return new Intl.NumberFormat(locale ?? getHumanLocale(), intlOptions);
}

function formatNumericValue(
  formatter: Intl.NumberFormat,
  value: NumberInput,
): string {
  if (value == null) return "";
  if (typeof value === "number" && !Number.isFinite(value)) return "";
  if (typeof value === "string" && !isNumericString(value)) return "";
  // ECMA-402 accepts decimal strings without coercing them through Number;
  // TypeScript's Intl declaration has not caught up with that native contract.
  const format = formatter.format as (input: number | bigint | string) => string;
  return format(value);
}

/** Build one formatter for repeated values while retaining the shared input rules. */
export function numberFormatter(
  options?: NumberFormatOptions,
): (value: NumberInput) => string {
  const formatter = intlNumberFormatter(options);
  return (value) => formatNumericValue(formatter, value);
}

/** Format a number-like value with native Intl options and no house defaults. */
export function formatNumber(
  value: NumberInput,
  options?: NumberFormatOptions,
): string {
  return formatNumericValue(intlNumberFormatter(options), value);
}
