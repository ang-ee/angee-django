import * as React from "react";

import { tv } from "../lib/variants";
import {
  DefinitionPair,
  type DefinitionPairValue,
} from "./DefinitionPair";

export const infoRowVariants = tv({
  base: "px-4 py-2 text-13",
});

export type InfoRowProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className"
> & {
  action?: React.ReactNode;
  className?: string;
  emptyValue?: React.ReactNode;
  label: React.ReactNode;
  value?: React.ReactNode;
};

export const InfoRow = React.forwardRef<HTMLDivElement, InfoRowProps>(
  function InfoRow(
    {
      action,
      className,
      emptyValue = "-",
      label,
      value,
      ...props
    },
    ref,
  ) {
    return (
      <DefinitionPair
        ref={ref}
        action={action}
        className={infoRowVariants({ className })}
        density="compact"
        emptyValue={emptyValue}
        label={label}
        layout="grid"
        orientation="inline"
        value={value}
        {...props}
      />
    );
  },
);
InfoRow.displayName = "InfoRow";

export type InfoRowValue = DefinitionPairValue;
