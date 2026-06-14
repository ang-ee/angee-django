import type { ReactNode } from "react";
import type { Tone } from "../../lib/tones";
import type { WidgetOption } from "../../widgets/types";

import { PAGE_ELEMENT_SLOT } from "./types";

export const COLUMN_SLOT = Symbol.for("@angee/base.page.column");

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

function ColumnMarker<
  TRow extends object = Record<string, unknown>,
>(_props: ColumnProps<TRow>): null {
  return null;
}

export const Column = Object.assign(ColumnMarker, {
  [PAGE_ELEMENT_SLOT]: "column" as const,
  [COLUMN_SLOT]: true,
});
