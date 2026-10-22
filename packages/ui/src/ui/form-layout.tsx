import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

/**
 * @kind token
 */
export const formLayoutVariants = tv({
  slots: {
    grid: "grid min-w-0",
    actions: "flex shrink-0 flex-wrap items-center",
    footer: "flex min-h-btn-md items-center",
    fieldRow: "block min-w-0",
  },
  variants: {
    density: {
      compact: {
        grid: "gap-3",
        actions: "gap-2",
        footer: "gap-2",
      },
      comfortable: {
        grid: "gap-4",
        actions: "gap-2",
        footer: "gap-3",
      },
      spacious: {
        grid: "gap-5",
        actions: "gap-3",
        footer: "gap-4",
      },
    },
    columns: {
      one: { grid: "grid-cols-1" },
      two: { grid: "lg:grid-cols-2" },
      adaptiveTwo: { grid: "" },
      three: { grid: "lg:grid-cols-2 xl:grid-cols-3" },
      four: { grid: "sm:grid-cols-2 xl:grid-cols-4" },
    },
    padding: {
      none: { grid: "" },
      sm: { grid: "p-3" },
      md: { grid: "p-4" },
      lg: { grid: "p-5" },
    },
    align: {
      start: {
        actions: "justify-start",
        footer: "justify-start",
      },
      end: {
        actions: "justify-end",
        footer: "justify-end",
      },
      between: {
        actions: "justify-between",
        footer: "justify-between",
      },
    },
    border: {
      none: { footer: "" },
      top: { footer: "border-t border-border-subtle" },
      bottom: { footer: "border-b border-border-subtle" },
    },
    surface: {
      transparent: { footer: "" },
      sheet: { footer: "bg-sheet" },
      canvas: { footer: "bg-canvas" },
    },
    span: {
      one: { fieldRow: "" },
      two: { fieldRow: "lg:col-span-2" },
      three: { fieldRow: "xl:col-span-3" },
      four: { fieldRow: "xl:col-span-4" },
      full: { fieldRow: "col-span-full" },
    },
  },
  defaultVariants: {
    align: "end",
    border: "none",
    columns: "one",
    density: "comfortable",
    padding: "none",
    span: "one",
    surface: "transparent",
  },
});

export type FormLayoutRecipeProps = VariantProps<typeof formLayoutVariants>;
export type FormFieldSpan = NonNullable<FormLayoutRecipeProps["span"]>;

export type FormGridAreas = readonly string[] | string;

export type FormGridProps = React.HTMLAttributes<HTMLDivElement> &
  Pick<FormLayoutRecipeProps, "columns" | "density" | "padding"> & {
    areas?: FormGridAreas;
    className?: string;
  };

/**
 * @kind primitive
 */
export const FormGrid = React.forwardRef<HTMLDivElement, FormGridProps>(
  function FormGrid(
    {
      areas,
      className,
      columns = "one",
      density = "comfortable",
      padding = "none",
      style,
      ...props
    },
    ref,
  ) {
    const styles = formLayoutVariants({ columns, density, padding });
    return (
      <div
        ref={ref}
        className={styles.grid({ className })}
        style={{
          ...gridAreaStyle(areas),
          ...(columns === "adaptiveTwo" ? { gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 22rem), 1fr))" } : undefined),
          ...style,
        }}
        {...props}
      />
    );
  },
);
FormGrid.displayName = "FormGrid";

export type FormActionsProps = React.HTMLAttributes<HTMLDivElement> &
  Pick<FormLayoutRecipeProps, "align" | "density"> & {
    className?: string;
  };

/**
 * @kind primitive
 */
export const FormActions = React.forwardRef<HTMLDivElement, FormActionsProps>(
  function FormActions(
    { align = "end", className, density = "comfortable", ...props },
    ref,
  ) {
    const styles = formLayoutVariants({ align, density });
    return (
      <div ref={ref} className={styles.actions({ className })} {...props} />
    );
  },
);
FormActions.displayName = "FormActions";

export type FormFooterProps = React.HTMLAttributes<HTMLElement> &
  Pick<FormLayoutRecipeProps, "align" | "border" | "density" | "surface"> & {
    className?: string;
    note?: React.ReactNode;
    noteClassName?: string;
  };

/**
 * @kind primitive
 */
export const FormFooter = React.forwardRef<HTMLElement, FormFooterProps>(
  function FormFooter(
    {
      align = "between",
      border = "none",
      children,
      className,
      density = "comfortable",
      note,
      noteClassName,
      surface = "transparent",
      ...props
    },
    ref,
  ) {
    const styles = formLayoutVariants({ align, border, density, surface });
    return (
      <footer ref={ref} className={styles.footer({ className })} {...props}>
        <div className={noteClassName ?? "min-w-0 text-12 text-fg-muted"}>
          {note}
        </div>
        <div className="flex shrink-0 items-center gap-2">{children}</div>
      </footer>
    );
  },
);
FormFooter.displayName = "FormFooter";

function gridAreaStyle(
  areas: FormGridAreas | undefined,
): React.CSSProperties | undefined {
  if (!areas) return undefined;
  const gridTemplateAreas =
    typeof areas === "string"
      ? areas
      : areas.map((row) => `"${row}"`).join(" ");
  return { gridTemplateAreas };
}

/** Places a field in a named `FormGrid` area.
 * @kind null
 * @reason Layout utility: computes a style, renders nothing.
 */
export function gridCellStyle(area: string | undefined): React.CSSProperties | undefined {
  if (!area) return undefined;
  return { gridArea: area };
}
