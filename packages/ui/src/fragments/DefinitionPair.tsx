import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { sectionEyebrowVariants } from "../ui/section-eyebrow";
import { textRoleVariants } from "../ui/text";

export const definitionPairVariants = tv({
  slots: {
    root: "min-w-0",
    label: sectionEyebrowVariants({ tracking: "normal", weight: "medium" }),
    value: "m-0 min-w-0 text-fg",
    detail: cn(textRoleVariants({ role: "caption", truncate: true }), "m-0 min-w-0"),
    action: "ml-2 inline-flex align-middle",
  },
  variants: {
    orientation: {
      inline: {
        label: "normal-case",
        detail: "col-start-2",
      },
      stacked: {
        root:
          "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1",
        label: "col-start-1 row-start-1 self-center",
        value: "col-span-2 col-start-1 row-start-2",
        detail: "col-span-2 col-start-1 row-start-3",
        action: "col-start-2 row-start-1 ml-0 self-center",
      },
    },
    layout: {
      contents: {},
      grid: {},
    },
    density: {
      cell: {
        root: "text-13",
        label: "text-2xs",
      },
      compact: {
        root: "text-13",
      },
      prominent: {
        root: "gap-y-3",
        label: "contents normal-case tracking-normal",
        value: "truncate text-2xl font-semibold tabular-nums",
        detail: "-mt-2",
      },
    },
  },
  compoundVariants: [
    {
      orientation: "inline",
      layout: "contents",
      class: {
        root: "contents",
        label: "max-w-[12rem] [overflow-wrap:anywhere]",
        value: "[overflow-wrap:anywhere]",
      },
    },
    {
      orientation: "inline",
      layout: "grid",
      class: {
        root:
          "grid grid-cols-[max-content_minmax(0,1fr)] items-start gap-x-3 gap-y-1",
        label: "pt-0.5",
        value: "break-words",
      },
    },
    {
      orientation: "stacked",
      density: "compact",
      class: {
        label: "font-semibold tracking-wide",
        value: "truncate font-medium",
      },
    },
  ],
  defaultVariants: {
    orientation: "inline",
    layout: "grid",
    density: "compact",
  },
});

type DefinitionPairRecipeProps = VariantProps<typeof definitionPairVariants>;

export type DefinitionPairOrientation = NonNullable<
  DefinitionPairRecipeProps["orientation"]
>;
export type DefinitionPairLayout = NonNullable<
  DefinitionPairRecipeProps["layout"]
>;
export type DefinitionPairDensity = NonNullable<
  DefinitionPairRecipeProps["density"]
>;
export type DefinitionPairElement = "div";

type DefinitionPairBaseProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "children" | "className"
> &
  DefinitionPairRecipeProps & {
    action?: React.ReactNode;
    className?: string;
    /** Muted caption content rendered after the value. */
    detail?: React.ReactNode;
    emptyValue?: React.ReactNode;
    value?: React.ReactNode;
  };

/** The default emits a dt/dd pair; `as="div"` emits divs and permits no label. */
export type DefinitionPairProps = DefinitionPairBaseProps & (
  | { as?: never; label: React.ReactNode }
  | { as: DefinitionPairElement; label?: React.ReactNode }
);

export type DefinitionPairValue = Pick<
  DefinitionPairBaseProps & { label: React.ReactNode },
  "action" | "label" | "value"
>;
export type DefinitionPairTuple = readonly [
  label: React.ReactNode,
  value: React.ReactNode,
];

type NormalizedDefinition<T> = T extends DefinitionPairTuple
  ? DefinitionPairValue & { label: T[0]; value: T[1] }
  : T;

export function definitionFromTuple<
  T extends DefinitionPairValue | DefinitionPairTuple,
>(definition: T): NormalizedDefinition<T> {
  if (isDefinitionPairTuple(definition)) {
    return {
      label: definition[0],
      value: definition[1],
    } as NormalizedDefinition<T>;
  }
  return definition as NormalizedDefinition<T>;
}

export const DefinitionPair = React.forwardRef<
  HTMLDivElement,
  DefinitionPairProps
>(function DefinitionPair(
  {
    action,
    as,
    className,
    density = "compact",
    detail,
    emptyValue = "-",
    label,
    layout = "grid",
    orientation = "inline",
    value,
    ...props
  },
  ref,
) {
  const generatedLabelId = React.useId();
  const styles = definitionPairVariants({ density, layout, orientation });
  const LabelElement = as === "div" ? "div" : "dt";
  const ValueElement = as === "div" ? "div" : "dd";
  const DetailElement = as === "div" ? "div" : "dd";
  const labelId = as === "div" && label != null ? generatedLabelId : undefined;
  const unlabelledDiv = as === "div" && labelId === undefined;
  const actionElement = action ? (
    <span className={styles.action()}>{action}</span>
  ) : null;

  return (
    <div
      ref={ref}
      className={styles.root({ className })}
      {...props}
      {...(labelId ? { role: "group", "aria-labelledby": labelId } : {})}
    >
      {label != null ? (
        <LabelElement id={labelId} className={styles.label()}>
          {label}
        </LabelElement>
      ) : null}
      {orientation === "stacked" ? actionElement : null}
      <ValueElement className={styles.value({
        className: unlabelledDiv
          ? `col-start-1 row-start-1 ${action ? "col-span-1" : "col-span-2"}`
          : undefined,
      })}>
        {orientation === "inline" ? (
          <>
            <span>{value ?? emptyValue}</span>
            {actionElement}
          </>
        ) : (
          value ?? emptyValue
        )}
      </ValueElement>
      {detail != null ? (
        <DetailElement className={styles.detail({
          className: unlabelledDiv
            ? "col-span-2 col-start-1 row-start-2"
            : undefined,
        })}>
          {detail}
        </DetailElement>
      ) : null}
    </div>
  );
});
DefinitionPair.displayName = "DefinitionPair";

function isDefinitionPairTuple(
  definition: DefinitionPairValue | DefinitionPairTuple,
): definition is DefinitionPairTuple {
  return Array.isArray(definition);
}
