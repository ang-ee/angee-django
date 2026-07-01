import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
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
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pageSizeOptions?: readonly number[];
  subject?: string;
  unit?: string;
  labelElement?: "button" | "span";
  labelClassName?: string;
  previousLabel?: string;
  nextLabel?: string;
  formatNumber?: (value: number) => string;
}

const DEFAULT_PAGE_SIZE_OPTIONS = [10, 20, 50, 80, 100, 200] as const;

/** The pager range-label recipe: an interactive `button` trigger vs a static `span`. */
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
  return String(value);
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
  const start = total === undefined || total === 0
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

export function Pager({
  page,
  pageSize,
  total,
  hasPrev,
  hasNext,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  subject,
  unit,
  labelElement = "button",
  labelClassName,
  previousLabel,
  nextLabel,
  formatNumber = defaultFormatNumber,
}: PagerProps): React.ReactElement {
  const t = useBaseT();
  const resolvedSubject = subject ?? t("pager.records");
  const [customPageSize, setCustomPageSize] = React.useState<number | null>(
    null,
  );
  const pageLabel = pagerRangeLabel({
    page,
    pageSize,
    total,
    unit,
    formatNumber,
  });
  const canPrev = hasPrev ?? page > 1;
  const canNext = hasNext ?? (total !== undefined && page * pageSize < total);
  const label = labelElement === "button" && onPageSizeChange
    ? (
      <PageSizePicker
        pageLabel={pageLabel}
        pageSize={pageSize}
        pageSizeOptions={pageSizeOptions}
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
        aria-label={previousLabel ?? t("pager.prev")}
        disabled={!canPrev}
        onClick={() => onPageChange?.(Math.max(1, page - 1))}
      >
        <Glyph name="chevron-left" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label={nextLabel ?? t("pager.next")}
        disabled={!canNext}
        onClick={() => onPageChange?.(page + 1)}
      >
        <Glyph name="chevron-right" />
      </Button>
    </>
  );
}

function PageSizePicker({
  pageLabel,
  pageSize,
  pageSizeOptions,
  subject,
  labelClassName,
  customPageSize,
  onCustomPageSizeChange,
  onPageSizeChange,
}: {
  pageLabel: string;
  pageSize: number;
  pageSizeOptions: readonly number[];
  subject: string;
  labelClassName?: string;
  customPageSize: number | null;
  onCustomPageSizeChange: (value: number | null) => void;
  onPageSizeChange: (pageSize: number) => void;
}): React.ReactElement {
  const t = useBaseT();
  const applyPageSize = React.useCallback(
    (value: number | null) => {
      if (typeof value !== "number" || !Number.isFinite(value) || value < 1) {
        return;
      }
      onPageSizeChange(Math.floor(value));
      onCustomPageSizeChange(null);
    },
    [onCustomPageSizeChange, onPageSizeChange],
  );

  return (
    <PopoverRoot>
      <PopoverTrigger
        className={pagerVariants({ label: "button", className: labelClassName })}
        aria-label={t("pager.pageOf", { subject, pageLabel })}
      >
        {pageLabel}
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner sideOffset={6} align="end">
          <PopoverContent className="w-56 p-3">
            <p className="mb-2 px-1 text-13 font-semibold text-fg">
              {t("pager.rowsPerPage")}
            </p>
            <div className="grid grid-cols-3 gap-1">
              {pageSizeOptions.map((value) => (
                <button
                  key={value}
                  type="button"
                  className={cn(
                    "h-7 rounded-6 px-2 text-13 tabular-nums outline-none transition-colors focus-visible:focus-ring",
                    value === pageSize
                      ? "bg-brand-soft font-medium text-brand-soft-text"
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
                value={customPageSize}
                size="sm"
                align="start"
                showStepper={false}
                className="min-w-0 flex-1"
                inputProps={{
                  "aria-label": t("pager.customRowsPerPage"),
                  placeholder: "42",
                }}
                onValueChange={onCustomPageSizeChange}
              />
              <Button type="submit" size="sm" variant="secondary">
                {t("pager.apply")}
              </Button>
            </form>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
