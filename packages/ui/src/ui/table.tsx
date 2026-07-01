import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { textRoleVariants } from "./text";

export const tableVariants = tv({
  slots: {
    table: "w-full border-collapse text-13",
    header: "",
    body: "",
    footer: "border-t border-border bg-sheet font-medium",
    row: "border-b border-border-subtle transition-colors",
    head:
      "h-10 border-b border-border bg-sheet px-3 py-0 text-left align-middle text-xs font-semibold leading-5 text-fg-muted whitespace-nowrap",
    cell: "h-10 px-3 align-middle",
    caption: cn(textRoleVariants({ role: "meta" }), "mt-4"),
  },
  variants: {
    interactive: {
      true: { row: "cursor-pointer hover:bg-sheet-2" },
      false: "",
    },
    selected: {
      true: { row: "bg-brand-soft hover:bg-brand-soft" },
      false: "",
    },
    sticky: {
      true: { head: "sticky top-0 z-sticky-cell" },
      false: "",
    },
  },
  defaultVariants: {
    interactive: false,
    selected: false,
    sticky: false,
  },
});

export type TableRecipeProps = VariantProps<typeof tableVariants>;

export type TableProps = React.TableHTMLAttributes<HTMLTableElement> & {
  className?: string;
};

export const Table = React.forwardRef<HTMLTableElement, TableProps>(
  function Table({ className, ...props }, ref) {
    const styles = tableVariants();
    return (
      <table
        ref={ref}
        className={styles.table({ className })}
        {...props}
      />
    );
  },
);
Table.displayName = "Table";

export type TableHeaderProps =
  React.HTMLAttributes<HTMLTableSectionElement> & {
    className?: string;
  };

export const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  TableHeaderProps
>(function TableHeader({ className, ...props }, ref) {
  const styles = tableVariants();
  return <thead ref={ref} className={styles.header({ className })} {...props} />;
});
TableHeader.displayName = "TableHeader";

export type TableBodyProps = React.HTMLAttributes<HTMLTableSectionElement> & {
  className?: string;
};

export const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  TableBodyProps
>(function TableBody({ className, ...props }, ref) {
  const styles = tableVariants();
  return <tbody ref={ref} className={styles.body({ className })} {...props} />;
});
TableBody.displayName = "TableBody";

export type TableFooterProps =
  React.HTMLAttributes<HTMLTableSectionElement> & {
    className?: string;
  };

export const TableFooter = React.forwardRef<
  HTMLTableSectionElement,
  TableFooterProps
>(function TableFooter({ className, ...props }, ref) {
  const styles = tableVariants();
  return <tfoot ref={ref} className={styles.footer({ className })} {...props} />;
});
TableFooter.displayName = "TableFooter";

export type TableRowProps = React.HTMLAttributes<HTMLTableRowElement> &
  Pick<TableRecipeProps, "interactive" | "selected"> & {
    className?: string;
  };

export const TableRow = React.forwardRef<HTMLTableRowElement, TableRowProps>(
  function TableRow(
    { className, interactive = false, selected = false, ...props },
    ref,
  ) {
    const styles = tableVariants({ interactive, selected });
    return <tr ref={ref} className={styles.row({ className })} {...props} />;
  },
);
TableRow.displayName = "TableRow";

export type TableHeadProps = React.ThHTMLAttributes<HTMLTableCellElement> &
  Pick<TableRecipeProps, "sticky"> & {
    className?: string;
  };

export const TableHead = React.forwardRef<
  HTMLTableCellElement,
  TableHeadProps
>(function TableHead({ className, sticky = false, ...props }, ref) {
  const styles = tableVariants({ sticky });
  return <th ref={ref} className={styles.head({ className })} {...props} />;
});
TableHead.displayName = "TableHead";

export type TableCellProps = React.TdHTMLAttributes<HTMLTableCellElement> & {
  className?: string;
};

export const TableCell = React.forwardRef<
  HTMLTableCellElement,
  TableCellProps
>(function TableCell({ className, ...props }, ref) {
  const styles = tableVariants();
  return <td ref={ref} className={styles.cell({ className })} {...props} />;
});
TableCell.displayName = "TableCell";

export type TableCaptionProps =
  React.HTMLAttributes<HTMLTableCaptionElement> & {
    className?: string;
  };

export const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  TableCaptionProps
>(function TableCaption({ className, ...props }, ref) {
  const styles = tableVariants();
  return (
    <caption
      ref={ref}
      className={styles.caption({ className })}
      {...props}
    />
  );
});
TableCaption.displayName = "TableCaption";
