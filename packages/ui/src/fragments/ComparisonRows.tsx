import * as React from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";

export interface ComparisonRow {
  key: React.Key;
  label: React.ReactNode;
  before: React.ReactNode;
  after: React.ReactNode;
  changed?: boolean;
  details?: React.ReactNode;
}

export interface ComparisonRowsProps {
  rows: readonly ComparisonRow[];
  beforeLabel: React.ReactNode;
  afterLabel: React.ReactNode;
  fieldLabel?: React.ReactNode;
  className?: string;
}

/** A compact, accessible before/after surface shared by review workflows. */
export function ComparisonRows({
  rows,
  beforeLabel,
  afterLabel,
  fieldLabel,
  className,
}: ComparisonRowsProps): React.ReactElement {
  return <div className={className}>
    <Table density="compact">
      <TableHeader>
        <TableRow>
          <TableHead>{fieldLabel}</TableHead>
          <TableHead>{beforeLabel}</TableHead>
          <TableHead>{afterLabel}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => <React.Fragment key={row.key}>
          <TableRow data-changed={row.changed || undefined} className={row.changed ? "bg-warning-soft/40" : undefined}>
            <TableCell className="align-top font-medium text-fg">{row.label}</TableCell>
            <TableCell className="min-w-0 align-top">{row.before}</TableCell>
            <TableCell className="min-w-0 align-top">{row.after}</TableCell>
          </TableRow>
          {row.details ? <TableRow>
            <TableCell colSpan={3} className="border-t-0 pt-0 text-fg-muted">{row.details}</TableCell>
          </TableRow> : null}
        </React.Fragment>)}
      </TableBody>
    </Table>
  </div>;
}
