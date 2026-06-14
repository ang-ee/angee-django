import * as React from "react";

import { toneFill } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

// Inline code is text-only, so its `tone` is a small local text-color map (not
// the bundled fill matrix, which would add a background). `box` is the
// orthogonal surface axis (renamed from `surface` so the word stays reserved
// for the `variant="surface"` fill elsewhere).
export const codeVariants = tv({
  base: "inline-flex max-w-full items-center rounded font-mono leading-none",
  variants: {
    tone: {
      neutral: "text-fg",
      muted: "text-fg-muted",
      success: "text-success-text",
      warning: "text-warning-text",
      danger: "text-danger-text",
      info: "text-info-text",
    },
    box: {
      none: "",
      inset: "bg-inset px-1.5 py-1",
      sheet: "bg-sheet px-1.5 py-1",
    },
    size: {
      sm: "text-2xs",
      md: "text-xs",
    },
    truncate: {
      true: "min-w-0 truncate",
      false: "",
    },
  },
  defaultVariants: {
    tone: "neutral",
    box: "none",
    size: "sm",
    truncate: false,
  },
});

export const codeBlockVariants = tv({
  base: "block max-w-full overflow-x-auto rounded-md border border-border-subtle bg-inset p-3 font-mono text-xs leading-5 text-fg",
  variants: {
    wrap: {
      true: "whitespace-pre-wrap break-words",
      false: "whitespace-pre",
    },
    tone: {
      neutral: "",
      muted: "text-fg-muted",
      danger: toneFill.danger.soft,
      success: toneFill.success.soft,
      warning: toneFill.warning.soft,
    },
  },
  defaultVariants: {
    wrap: false,
    tone: "neutral",
  },
});

type CodeRecipeProps = VariantProps<typeof codeVariants>;
type CodeBlockRecipeProps = VariantProps<typeof codeBlockVariants>;

export type CodeTone = NonNullable<CodeRecipeProps["tone"]>;
export type CodeBox = NonNullable<CodeRecipeProps["box"]>;
export type CodeSize = NonNullable<CodeRecipeProps["size"]>;
export type CodeBlockTone = NonNullable<CodeBlockRecipeProps["tone"]>;

export type CodeProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "color"
> &
  CodeRecipeProps & {
    className?: string;
  };

export const Code = React.forwardRef<HTMLElement, CodeProps>(function Code(
  {
    className,
    box = "none",
    size = "sm",
    truncate = false,
    tone = "neutral",
    ...props
  },
  ref,
) {
  return (
    <code
      ref={ref}
      className={codeVariants({ className, box, size, truncate, tone })}
      {...props}
    />
  );
});
Code.displayName = "Code";

export type CodeBlockProps = Omit<
  React.HTMLAttributes<HTMLPreElement>,
  "className" | "color"
> &
  CodeBlockRecipeProps & {
    className?: string;
  };

export const CodeBlock = React.forwardRef<HTMLPreElement, CodeBlockProps>(
  function CodeBlock(
    { children, className, tone = "neutral", wrap = false, ...props },
    ref,
  ) {
    return (
      <pre
        ref={ref}
        className={codeBlockVariants({ className, tone, wrap })}
        {...props}
      >
        <code>{children}</code>
      </pre>
    );
  },
);
CodeBlock.displayName = "CodeBlock";
