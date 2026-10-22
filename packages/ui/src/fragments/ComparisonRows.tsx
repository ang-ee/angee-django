import * as React from "react";

import { RadioGroup } from "../ui/radio-group";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";

export interface ComparisonColumn {
  key: string;
  label: React.ReactNode;
}

export interface ComparisonRow {
  key: React.Key;
  label: React.ReactNode;
  /** The row's value per column key. */
  cells: Readonly<Record<string, React.ReactNode>>;
  changed?: boolean;
  details?: React.ReactNode;
}

export interface ComparisonChoice {
  /** The chosen column key per row key; a row without one shows no choice. */
  value: Readonly<Record<string, string>>;
  onChange: (rowKey: string, columnKey: string) => void;
  /** Names each row's choice for assistive technology, from its label. */
  label: (row: ComparisonRow) => string;
}

export interface ComparisonRowsProps {
  rows: readonly ComparisonRow[];
  /** The compared columns, in order; two for a before/after review, N for a merge. */
  columns: readonly ComparisonColumn[];
  fieldLabel?: React.ReactNode;
  /** Lets the reader pick one column per row (a merge's surviving value). */
  choice?: ComparisonChoice;
  className?: string;
}

/** A compact, accessible comparison of one field per row across two or more columns,
 * with an optional per-row choice of the value that wins; the one surface review and
 * merge workflows share.
 * @kind fragment
 */
export function ComparisonRows({
  rows,
  columns,
  fieldLabel,
  choice,
  className,
}: ComparisonRowsProps): React.ReactElement {
  return <div className={className}>
    <Table density="compact">
      <TableHeader>
        <TableRow>
          <TableHead>{fieldLabel}</TableHead>
          {columns.map((column) => <TableHead key={column.key}>{column.label}</TableHead>)}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const rowKey = String(row.key);
          const chosen = choice?.value[rowKey];
          const cells = columns.map((column) => (
            <TableCell key={column.key} className="min-w-0 align-top">
              {choice ? (
                <RadioGroup.Item value={column.key} label={row.cells[column.key]} />
              ) : row.cells[column.key]}
            </TableCell>
          ));
          return <React.Fragment key={row.key}>
            {choice ? (
              <RadioGroup
                render={<TableRow data-changed={row.changed || undefined} className={row.changed ? "bg-warning-soft/40" : undefined} />}
                aria-label={choice.label(row)}
                orientation="horizontal"
                value={chosen ?? null}
                onValueChange={(value) => { if (typeof value === "string") choice.onChange(rowKey, value); }}
              >
                <TableCell className="align-top font-medium text-fg">{row.label}</TableCell>
                {cells}
              </RadioGroup>
            ) : (
              <TableRow data-changed={row.changed || undefined} className={row.changed ? "bg-warning-soft/40" : undefined}>
                <TableCell className="align-top font-medium text-fg">{row.label}</TableCell>
                {cells}
              </TableRow>
            )}
            {row.details ? <TableRow>
              <TableCell colSpan={columns.length + 1} className="border-t-0 pt-0 text-fg-muted">{row.details}</TableCell>
            </TableRow> : null}
          </React.Fragment>;
        })}
      </TableBody>
    </Table>
  </div>;
}
