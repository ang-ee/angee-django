import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { tv } from "../lib/variants";
import { textRoleVariants } from "../ui/text";

export const inlineEmptyVariants = tv({
  slots: {
    root: cn(
      textRoleVariants({ role: "meta" }),
      "flex min-w-0 items-center justify-center gap-2 px-4 py-6 text-center",
    ),
    icon: "shrink-0 [&_.glyph]:size-3.5 [&>svg]:size-3.5",
    label: "min-w-0 truncate",
  },
});

export type InlineEmptyProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className"
> & {
  className?: string;
  icon?: React.ReactNode | string;
  label: React.ReactNode;
};

export const InlineEmpty = React.forwardRef<HTMLDivElement, InlineEmptyProps>(
  function InlineEmpty({ className, icon, label, ...props }, ref) {
    const styles = inlineEmptyVariants();

    return (
      <div ref={ref} className={styles.root({ className })} {...props}>
        {icon ? <span className={styles.icon()}>{renderGlyph(icon)}</span> : null}
        <span className={styles.label()}>{label}</span>
      </div>
    );
  },
);
InlineEmpty.displayName = "InlineEmpty";
