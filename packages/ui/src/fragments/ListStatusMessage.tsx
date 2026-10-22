import * as React from "react";

import { cn } from "../lib/cn";
import { toneText, type Tone } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

/**
 * @kind token
 */
export const listStatusMessageVariants = tv({
  base: "text-13",
  variants: {
    align: {
      start: "",
      center: "text-center",
    },
    density: {
      inline: "",
      row: "py-3",
    },
  },
  defaultVariants: {
    align: "start",
    density: "inline",
  },
});

export type ListStatusMessageProps = Omit<React.HTMLAttributes<HTMLDivElement>, "className" | "role"> &
  VariantProps<typeof listStatusMessageVariants> & {
    className?: string;
    /** `danger` announces as an alert in danger ink; anything else as a muted status. */
    tone?: Tone;
  };

/**
 * A list's in-flow message row: an error, a refusal or a note that applies to the
 * rows around it (a grouped table, a board lane, a pane list). One owner for the
 * tone → role/ink decision every list surface used to make itself.
 * @kind fragment
 */
export const ListStatusMessage = React.forwardRef<HTMLDivElement, ListStatusMessageProps>(
  function ListStatusMessage({ align, density, tone = "muted", className, ...props }, ref) {
    const danger = tone === "danger";
    return (
      <div
        ref={ref}
        role={danger ? "alert" : "status"}
        className={cn(listStatusMessageVariants({ align, density }), toneText(danger ? "danger" : "muted"), className)}
        {...props}
      />
    );
  },
);
ListStatusMessage.displayName = "ListStatusMessage";
