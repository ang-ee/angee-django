import * as React from "react";
import { Link } from "@tanstack/react-router";
import { type Table as TableModel } from "@tanstack/react-table";
import type { Row } from "@angee/metadata";
import { Spinner } from "../../../ui/spinner";
import { Glyph } from "../../../chrome/Glyph";
import { EmptyState } from "../../../fragments/EmptyState";
import { useUiT } from "../../../i18n";
import { cn } from "../../../lib/cn";
import { Button, buttonVariants, type ButtonVariant } from "../../../ui/button";
import { Skeleton } from "../../../ui/skeleton";
import { TableCell, TableRow } from "../../../ui/table";
import { textRoleVariants } from "../../../ui/text";
import type { ListEmptyAction, ListEmptyContent, ListEmptyState } from "../resource-view-types";
import { alignOf } from "./cell-utils";
import { ALIGN_CLASS } from "./types";
/** The flush "Loading…" footer shown under a list layout while a page fetches. */
export function ListLoadingFooter(): React.ReactElement {
  const t = useUiT();
  return (
    <div className={cn(textRoleVariants({ role: "meta" }), "flex items-center justify-center gap-2 border-t border-border px-3 py-4")}>
      <Spinner size="sm" />
      {t("list.loading")}
    </div>
  );
}

/** Table-shaped loading body used while a list fetches its first page. */
export function ListSkeletonRows<TRow extends Row>({
  table,
  selectable = true,
  rowCount = 8,
  loadingLabel,
  trailingColumn = false,
}: {
  table: TableModel<TRow>;
  selectable?: boolean;
  rowCount?: number;
  loadingLabel?: React.ReactNode;
  trailingColumn?: boolean;
}): React.ReactElement {
  const columns = table.getVisibleLeafColumns();
  const colSpan = columns.length + (selectable ? 1 : 0) + (trailingColumn ? 1 : 0);
  return (
    <>
      {loadingLabel ? (
        <TableRow>
          <TableCell
            aria-busy="true"
            aria-live="polite"
            className="sr-only"
            colSpan={colSpan}
            role="status"
          >
            {loadingLabel}
          </TableCell>
        </TableRow>
      ) : null}
      {Array.from({ length: Math.max(1, rowCount) }, (_, rowIndex) => (
        <TableRow key={rowIndex} aria-hidden="true">
          {selectable ? (
            <TableCell className="w-8">
              <Skeleton className="size-3.5 rounded-[3px]" />
            </TableCell>
          ) : null}
          {columns.map((column, columnIndex) => {
            const align = alignOf(column.columnDef);
            return (
              <TableCell key={column.id} className={ALIGN_CLASS[align]}>
                <Skeleton
                  shape="text"
                  size="sm"
                  className={cn(
                    skeletonCellWidth(rowIndex + columnIndex),
                    align === "right" && "ml-auto",
                    align === "center" && "mx-auto",
                  )}
                />
              </TableCell>
            );
          })}
          {trailingColumn ? (
            <TableCell className="text-right">
              <Skeleton shape="text" size="sm" className="ml-auto w-8" />
            </TableCell>
          ) : null}
        </TableRow>
      ))}
    </>
  );
}

function skeletonCellWidth(index: number): string {
  const widths = ["w-4/5", "w-2/3", "w-1/2", "w-24", "w-32"] as const;
  return widths[index % widths.length] ?? "w-2/3";
}

/** The centered empty body shared by the table, gallery, timeline, tree, and board views. */
export function ListEmpty({
  children,
  className,
}: {
  children: ListEmptyContent;
  className?: string;
}): React.ReactElement {
  if (isListEmptyState(children)) {
    return (
      <div
        className={cn(
          "grid h-full place-content-center text-center",
          className,
        )}
      >
        <EmptyState
          actions={children.actions ?? renderListEmptyAction(children.action)}
          className="min-h-0 p-6 shadow-none"
          description={children.description}
          icon={children.icon}
          title={children.title}
        />
      </div>
    );
  }

  return (
    <div
      className={cn(
        textRoleVariants({ role: "meta" }),
        "grid h-full place-content-center text-center",
        className,
      )}
    >
      {children}
    </div>
  );
}

function isListEmptyState(value: ListEmptyContent): value is ListEmptyState {
  return (
    typeof value === "object"
    && value !== null
    && !Array.isArray(value)
    && !React.isValidElement(value)
    && "title" in value
  );
}

function renderListEmptyAction(
  action: ListEmptyAction | undefined,
): React.ReactNode {
  if (!action) return null;
  const variant: ButtonVariant = action.variant ?? "primary";
  const content = (
    <>
      {typeof action.icon === "string" ? <Glyph name={action.icon} /> : action.icon}
      {action.label}
    </>
  );
  if (action.href) {
    if (isInternalHref(action.href)) {
      return (
        <Link className={buttonVariants({ variant })} to={action.href}>
          {content}
        </Link>
      );
    }
    return (
      <a className={buttonVariants({ variant })} href={action.href}>
        {content}
      </a>
    );
  }
  return (
    <Button onClick={action.onClick} variant={variant}>
      {content}
    </Button>
  );
}

function isInternalHref(href: string): boolean {
  return href.startsWith("/") && !href.startsWith("//");
}
