import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";
import { Banner } from "../ui/alert";
import { Button, type ButtonProps } from "../ui/button";

export const brandButtonVariants = tv({
  slots: {
    group: "grid gap-2",
    button: "w-full justify-center",
    icon:
      "grid size-5 shrink-0 place-content-center text-fg-2 [&_svg]:size-5",
    content: "min-w-0 text-left",
    label: "block truncate",
    description: "mt-0.5 block truncate text-2xs font-normal opacity-80",
    error: "rounded-6 border-x border-t px-3 py-2 text-left text-xs",
  },
  variants: {
    size: {
      md: {
        button: "min-h-btn-md",
        icon: "size-4 [&_svg]:size-4",
      },
      lg: {
        button: "min-h-btn-lg",
      },
    },
    tone: {
      neutral: "",
      google: "",
      github: "",
      microsoft: "",
      apple: "",
      brand: {
        icon: "text-brand",
      },
    },
  },
  defaultVariants: {
    size: "lg",
    tone: "neutral",
  },
});

type BrandButtonRecipeProps = VariantProps<typeof brandButtonVariants>;

export type BrandButtonTone = NonNullable<BrandButtonRecipeProps["tone"]>;
export type BrandButtonSize = NonNullable<BrandButtonRecipeProps["size"]>;
export type BrandButtonVariant = "brand" | "secondary";

export type BrandButtonProps = Omit<
  ButtonProps,
  "children" | "size" | "variant"
> &
  BrandButtonRecipeProps & {
    label: React.ReactNode;
    description?: React.ReactNode;
    icon?: React.ReactNode;
    error?: React.ReactNode;
    errorId?: string;
    variant?: BrandButtonVariant;
  };

export function BrandButton({
  className,
  description,
  error,
  errorId,
  icon,
  label,
  size = "lg",
  tone = "neutral",
  variant = "secondary",
  ...props
}: BrandButtonProps): React.ReactElement {
  const styles = brandButtonVariants({ size, tone });
  const describedBy = props["aria-describedby"];
  const buttonDescribedBy =
    error && errorId
      ? [describedBy, errorId].filter(Boolean).join(" ")
      : describedBy;

  return (
    <>
      <Button
        aria-describedby={buttonDescribedBy}
        className={styles.button({ className })}
        size={size}
        variant={variant === "brand" ? "primary" : "secondary"}
        {...props}
      >
        {icon ? <span className={styles.icon()}>{icon}</span> : null}
        <span className={styles.content()}>
          <span className={styles.label()}>{label}</span>
          {description ? (
            <span className={styles.description()}>{description}</span>
          ) : null}
        </span>
      </Button>
      {error ? (
        <Banner
          className={styles.error()}
          icon={false}
          id={errorId}
          tone="danger"
          format="alert"
        >
          {error}
        </Banner>
      ) : null}
    </>
  );
}

export interface BrandButtonGroupProps {
  children: React.ReactNode;
  className?: string;
}

export function BrandButtonGroup({
  children,
  className,
}: BrandButtonGroupProps): React.ReactElement {
  const styles = brandButtonVariants();
  return <div className={styles.group({ className })}>{children}</div>;
}

