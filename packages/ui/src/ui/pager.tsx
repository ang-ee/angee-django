import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { formatNumber as formatHumanNumber } from "../lib/format-number";
import { tv } from "../lib/variants";
import { Button } from "./button";
import { NumberField } from "./number-field";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "./popover";

export interface PagerState {
  page: number;
  pageSize: number;
  total: number | undefined;
  hasPrev?: boolean;
  hasNext?: boolean;
}

export interface PagerProps extends PagerState {
  /**
   * `range` (the default) reads "1-20 / 120 records" over pages of `pageSize`;
   * `record` reads "n / N" over single records (a record page's pager), with
   * `page` the current position and no page size.
   */
  mode?: "range" | "record";
  disabled?: boolean;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pageSizeOptions?: readonly number[];
  maxPageSize?: number;
  subject?: string;
  unit?: string;
  labelElement?: "button" | "span";
  labelClassName?: string;
  previousLabel?: string;
  nextLabel?: string;
  formatNumber?: (value: number) => string;
}

const DEFAULT_PAGE_SIZE_OPTIONS = [10, 20, 50, 80, 100, 200] as const;

/** The pager range-label recipe: an interactive `button` trigger vs a static `span`.
 * @kind token
 */
export const pagerVariants = tv({
  base: "tabular-nums",
  variants: {
    label: {
      button:
        "h-6 rounded-6 px-1.5 text-13 text-fg outline-none hover:bg-inset focus-visible:focus-ring",
      span: "",
    },
  },
  defaultVariants: { label: "span" },
});

function defaultFormatNumber(value: number): string {
  return formatHumanNumber(value);
}

function pagerRangeLabel({
  page,
  pageSize,
  total,
  unit,
  formatNumber,
}: {
  page: number;
  pageSize: number;
  total: number | undefined;
  unit: string | undefined;
  formatNumber: (value: number) => string;
}): string {
  const start = total === 0
    ? 0
    : (page - 1) * pageSize + 1;
  const end = total === undefined
    ? page * pageSize
    : Math.min(total, page * pageSize);
  return `${formatNumber(start)}-${formatNumber(end)}${
    total !== undefined
      ? ` / ${formatNumber(total)}${unit ? ` ${unit}` : ""}`
      : ""
  }`;
}

/**
 * @kind primitive
 */
export function Pager({
  mode = "range",
  page,
  pageSize,
  total,
  hasPrev,
  hasNext,
  disabled = false,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  maxPageSize,
  subject,
  unit,
  labelElement = "button",
  labelClassName,
  previousLabel,
  nextLabel,
  formatNumber = defaultFormatNumber,
}: PagerProps): React.ReactElement {
  const t = useUiT();
  const resolvedSubject = subject ?? t("pager.records");
  const [customPageSize, setCustomPageSize] = React.useState<number | null>(
    null,
  );
  const pageLabel = mode === "record"
    ? `${page > 0 ? formatNumber(page) : ""} / ${total !== undefined ? formatNumber(total) : "?"}`.trimStart()
    : pagerRangeLabel({ page, pageSize, total, unit, formatNumber });
  const canPrev = hasPrev ?? page > 1;
  const canNext = hasNext ?? (total !== undefined && page * pageSize < total);
  const label = mode === "record"
    ? (
      <span className={cn(pagerVariants({ label: "span" }), "whitespace-nowrap text-13 text-fg-muted", labelClassName)}>
        {page > 0 ? <><span className="font-medium text-fg">{formatNumber(page)}</span> / {total !== undefined ? formatNumber(total) : "?"}</> : pageLabel}
      </span>
    )
    : labelElement === "button" && onPageSizeChange
    ? (
      <PageSizePicker
        disabled={disabled}
        pageLabel={pageLabel}
        pageSize={pageSize}
        pageSizeOptions={pageSizeOptions}
        maxPageSize={maxPageSize}
        subject={resolvedSubject}
        labelClassName={labelClassName}
        customPageSize={customPageSize}
        onCustomPageSizeChange={setCustomPageSize}
        onPageSizeChange={onPageSizeChange}
      />
    )
    : labelElement === "button"
      ? (
        <button
          type="button"
          disabled={disabled}
          className={pagerVariants({ label: "button", className: labelClassName })}
          aria-label={t("pager.pageOf", { subject: resolvedSubject, pageLabel })}
        >
          {pageLabel}
        </button>
      )
      : (
        <span className={pagerVariants({ label: "span", className: labelClassName })}>
          {pageLabel}
        </span>
      );

  return (
    <>
      {label}
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label={previousLabel ?? t(mode === "record" ? "recordPager.prev" : "pager.prev")}
        disabled={disabled || !canPrev}
        // Record mode reports the step (page − 1) even from an unknown position (0), so the
        // record pager can tell the way from the number.
        onClick={() => onPageChange?.(mode === "record" ? page - 1 : Math.max(1, page - 1))}
      >
        <Glyph name="chevron-left" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label={nextLabel ?? t(mode === "record" ? "recordPager.next" : "pager.next")}
        disabled={disabled || !canNext}
        onClick={() => onPageChange?.(page + 1)}
      >
        <Glyph name="chevron-right" />
      </Button>
    </>
  );
}

function PageSizePicker({
  disabled,
  pageLabel,
  pageSize,
  pageSizeOptions,
  maxPageSize,
  subject,
  labelClassName,
  customPageSize,
  onCustomPageSizeChange,
  onPageSizeChange,
}: {
  disabled: boolean;
  pageLabel: string;
  pageSize: number;
  pageSizeOptions: readonly number[];
  maxPageSize: number | undefined;
  subject: string;
  labelClassName?: string;
  customPageSize: number | null;
  onCustomPageSizeChange: (value: number | null) => void;
  onPageSizeChange: (pageSize: number) => void;
}): React.ReactElement {
  const t = useUiT();
  const applyPageSize = React.useCallback(
    (value: number | null) => {
      if (typeof value !== "number" || !Number.isFinite(value) || value < 1) {
        return;
      }
      if (disabled) return;
      onPageSizeChange(Math.min(maxPageSize ?? value, Math.floor(value)));
      onCustomPageSizeChange(null);
    },
    [disabled, maxPageSize, onCustomPageSizeChange, onPageSizeChange],
  );

  return (
    <PopoverRoot>
      <PopoverTrigger
        disabled={disabled}
        className={pagerVariants({ label: "button", className: labelClassName })}
        aria-label={t("pager.pageOf", { subject, pageLabel })}
      >
        {pageLabel}
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner sideOffset={6} align="end">
          <PopoverContent className="w-56 p-3">
            <p className="mb-2 px-1 text-13 font-semibold text-fg">
              {t("pager.pageSize")}
            </p>
            <div className="grid grid-cols-3 gap-1">
              {pageSizeOptions.filter((value) => maxPageSize === undefined || value <= maxPageSize).map((value) => (
                <button
                  key={value}
                  disabled={disabled}
                  type="button"
                  className={cn(
                    "h-control-xs rounded-6 px-2 text-13 tabular-nums outline-none transition-colors focus-visible:focus-ring",
                    value === pageSize
                      ? "selected font-medium"
                      : "text-fg hover:bg-inset",
                  )}
                  onClick={() => applyPageSize(value)}
                >
                  {value}
                </button>
              ))}
            </div>
            <form
              className="mt-3 flex min-w-0 items-center gap-2 border-t border-border-subtle pt-3"
              onSubmit={(event) => {
                event.preventDefault();
                applyPageSize(customPageSize);
              }}
            >
              <NumberField
                min={1}
                max={maxPageSize}
                value={customPageSize}
                size="sm"
                align="start"
                showStepper={false}
                className="min-w-0 flex-1"
                inputProps={{
                  "aria-label": t("pager.customPageSize"),
                  placeholder: "42",
                }}
                onValueChange={onCustomPageSizeChange}
              />
              <Button type="submit" size="sm" variant="secondary" disabled={disabled}>
                {t("pager.apply")}
              </Button>
            </form>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
