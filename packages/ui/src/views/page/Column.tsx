import type { ReactNode } from "react";
import type { Tone } from "../../lib/tones";
import type { WidgetOption } from "../../widgets/types";

import { PAGE_ELEMENT_SLOT } from "./types";

export type PageColumnAlign = "left" | "center" | "right";
export type ColumnAggregate =
  | "count"
  | "sum"
  | "avg"
  | "min"
  | "max"
  | (string & {});

export interface ColumnProps<
  TRow extends object = Record<string, unknown>,
> {
  field: string;
  header?: ReactNode;
  widget?: string;
  /** Options passed to enum-like cell widgets; derived from SDL when omitted. */
  options?: readonly WidgetOption[];
  sortable?: boolean;
  aggregate?: ColumnAggregate;
  align?: PageColumnAlign;
  render?: (row: TRow) => ReactNode;
  tone?: Record<string, Tone>;
}

export interface ColumnDescriptor<
  TRow extends object = Record<string, unknown>,
> {
  field: string;
  header?: ReactNode;
  widget?: string;
  /** Options passed to enum-like cell widgets; derived from SDL when omitted. */
  options?: readonly WidgetOption[];
  sortable?: boolean;
  aggregate?: ColumnAggregate;
  align?: PageColumnAlign;
  render?: (row: TRow) => ReactNode;
  tone?: Record<string, Tone>;
}

/**
 * The tone a column's `tone` map assigns to a cell value: the descriptor answers
 * about its own value→tone vocabulary (a nullish value reads as the empty label),
 * falling back to `neutral`. The one owner of the `column.tone[label] ?? "neutral"`
 * read — both the table cell and the board lane dot route through it. Returns
 * `undefined` when the column declares no tone map (the caller renders plainly).
 */
export function columnTone<TRow extends object>(
  column: ColumnDescriptor<TRow>,
  value: unknown,
): Tone | undefined {
  if (!column.tone) return undefined;
  const label = value == null ? "" : String(value);
  return column.tone[label] ?? "neutral";
}

function ColumnMarker<
  TRow extends object = Record<string, unknown>,
>(_props: ColumnProps<TRow>): null {
  return null;
}

export const Column = Object.assign(ColumnMarker, {
  [PAGE_ELEMENT_SLOT]: "column" as const,
});
