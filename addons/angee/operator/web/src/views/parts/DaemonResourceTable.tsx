import {
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  type ButtonProps,
} from "@angee/base";
import type { ReactNode } from "react";

/** A single resource column: its header and how to render a row's cell. */
export interface DaemonResourceColumn<Row> {
  header: ReactNode;
  cell: (row: Row) => ReactNode;
  /** Right-align the header and cell (numeric/trailing columns). */
  align?: "end";
}

/** A row action rendered as a button in the trailing right-aligned actions cell. */
export interface DaemonResourceAction<Row> {
  label: ReactNode;
  variant?: ButtonProps["variant"];
  run: (row: Row) => void | Promise<unknown>;
}

export interface DaemonResourceTableProps<Row> {
  columns: readonly DaemonResourceColumn<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  /** When present, a trailing right-aligned actions column of buttons per row. */
  actions?: readonly DaemonResourceAction<Row>[];
  /** Header for the trailing actions column (translated by the section, required with `actions`). */
  actionsLabel?: ReactNode;
  /** Disables every action button (an action is in flight). */
  busy?: boolean;
  /** Shown centered across all columns when `rows` is empty. */
  emptyMessage: ReactNode;
}

/**
 * The shared daemon resource table for operator console sections: the `<Table>`
 * scaffold, header, body, empty row, and the trailing actions column. Sections
 * declare only their columns, rows, and actions; they keep their own
 * `OperatorSection` wrapper, mutations, `busy` fold, and `runDaemonAction`
 * wiring. The empty-state colSpan spans every column, actions included.
 */
export function DaemonResourceTable<Row>({
  columns,
  rows,
  rowKey,
  actions,
  actionsLabel,
  busy = false,
  emptyMessage,
}: DaemonResourceTableProps<Row>): ReactNode {
  const colSpan = columns.length + (actions ? 1 : 0);

  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((column, index) => (
            <TableHead className={column.align === "end" ? "text-right" : undefined} key={index}>
              {column.header}
            </TableHead>
          ))}
          {actions ? <TableHead className="text-right">{actionsLabel}</TableHead> : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 ? (
          <TableRow>
            <TableCell className="text-center text-13 text-fg-muted" colSpan={colSpan}>
              {emptyMessage}
            </TableCell>
          </TableRow>
        ) : (
          rows.map((row) => (
            <TableRow key={rowKey(row)}>
              {columns.map((column, index) => (
                <TableCell
                  className={column.align === "end" ? "text-right" : undefined}
                  key={index}
                >
                  {column.cell(row)}
                </TableCell>
              ))}
              {actions ? (
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    {actions.map((action, index) => (
                      <Button
                        disabled={busy}
                        key={index}
                        onClick={() => void action.run(row)}
                        size="sm"
                        variant={action.variant}
                      >
                        {action.label}
                      </Button>
                    ))}
                  </div>
                </TableCell>
              ) : null}
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}
