import * as React from "react";

import { tv } from "../lib/variants";
import { SectionEyebrow } from "../ui/section-eyebrow";

export const infoRowVariants = tv({
  slots: {
    root:
      "grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] items-start gap-x-3 gap-y-1 px-4 py-2 text-13",
    label: "pt-0.5 normal-case tracking-normal",
    value: "m-0 min-w-0 break-words text-fg",
    action: "ml-2 inline-flex align-middle",
  },
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
    const styles = infoRowVariants();

    return (
      <div ref={ref} className={styles.root({ className })} {...props}>
        <SectionEyebrow
          as="dt"
          className={styles.label()}
          tracking="normal"
          weight="medium"
        >
          {label}
        </SectionEyebrow>
        <dd className={styles.value()}>
          <span>{value ?? emptyValue}</span>
          {action ? <span className={styles.action()}>{action}</span> : null}
        </dd>
      </div>
    );
  },
);
InfoRow.displayName = "InfoRow";

export type InfoRowValue = Pick<InfoRowProps, "action" | "label" | "value">;
