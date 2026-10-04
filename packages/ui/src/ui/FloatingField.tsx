/**
 * FloatingField — an Input wrapper with an animated floating label.
 *
 * The label starts inside the control as a placeholder substitute. When the
 * input is focused or has a value it animates upward to sit on the border,
 * identical to the modern Material-style interaction without any JavaScript
 * state — purely CSS :placeholder-shown / :not(:placeholder-shown) +
 * :focus-within.
 *
 * Design decisions:
 * - Does NOT replace Field/FieldRow — it lives alongside them as an
 *   additive variant. Existing forms are unaffected.
 * - Uses the existing token vocabulary (--border-focus, --text-muted, etc.).
 *   No new tokens required.
 * - The floating label reuses rounded-6 / rounded-4 consistent with the
 *   global radius preset (inherits via CSS var).
 * - Accessible: <label> is properly associated via htmlFor. The hidden
 *   placeholder=" " is the CSS hook — it does NOT show as visible text.
 * - motion-safe gated: the label transition only runs when
 *   prefers-reduced-motion: no-preference.
 */

import * as React from "react";

import { cn } from "../lib/cn";
import { tv } from "../lib/variants";
import { OptionalHint, RequiredMark } from "./label";
import { widgetControlSurface } from "./widget-control";

// ─── Recipe ──────────────────────────────────────────────────────────────────

const floatingFieldVariants = tv({
  slots: {
    // The relative wrapper that establishes the stacking context for the label.
    root: "relative w-full",

    // The <input> itself. Has an invisible space placeholder so
    // :placeholder-shown is active when value is empty — this drives the CSS.
    input: [
      "peer w-full rounded-6 border bg-sheet px-3 text-fg",
      "placeholder-transparent",          // hide the space placeholder visually
      "transition-colors outline-none",
      // focus
      "focus:border-border-focus focus:focus-ring",
      // invalid (driven by aria-invalid)
      "aria-[invalid=true]:border-danger",
      "aria-[invalid=true]:focus:border-danger aria-[invalid=true]:focus:focus-ring-danger",
      // disabled
      "disabled:cursor-not-allowed disabled:opacity-60",
    ],

    // The <label> floats above the border when active / filled.
    // CSS peer-placeholder-shown targets the input's :placeholder-shown state.
    label: [
      // Base position: sits on the top border, scale(1) small text.
      "pointer-events-none absolute left-3 select-none font-medium",
      "text-fg-muted",
      // When the peer's placeholder IS shown (empty, unfocused) → inside the input.
      "peer-placeholder-shown:top-1/2 peer-placeholder-shown:-translate-y-1/2 peer-placeholder-shown:text-13 peer-placeholder-shown:text-fg-subtle",
      // When the peer's placeholder is NOT shown (has value) OR on focus →
      // sits on top border, small size. We simulate the focus case via
      // peer-focus together with :not(:placeholder-shown) in compound.
      "top-0 -translate-y-1/2 text-2xs",
      // The label background "cuts" through the border line.
      "rounded-sm bg-sheet px-1",
      // Smooth motion — only when reduced-motion is off.
      "motion-safe:transition-[top,font-size,transform,color] motion-safe:duration-150 motion-safe:ease-out",
      // On focus: upgrade text color to brand regardless of value.
      "peer-focus:top-0 peer-focus:-translate-y-1/2 peer-focus:text-2xs peer-focus:text-brand",
      // When invalid: label turns danger on focus
      "peer-aria-[invalid=true]:peer-focus:text-danger-text",
    ],

    // Optional description / helper text below the field.
    description: "mt-1 text-xs leading-5 text-fg-muted",

    // Validation error text.
    error: "mt-1 text-xs leading-5 text-danger-text",

    // Required / optional indicator row.
    header: "mb-0",
  },
  variants: {
    size: {
      sm: {
        input: "h-btn-sm text-xs",
        label: "text-2xs",
      },
      md: {
        input: "h-input-h pb-0 pt-0 text-13",
        label: "text-2xs",
      },
      lg: {
        input: "h-input-h-lg px-3 text-sm",
        label: "text-xs",
      },
    },
  },
  defaultVariants: {
    size: "md",
  },
});

// ─── Types ───────────────────────────────────────────────────────────────────

export interface FloatingFieldProps
  extends Omit<
    React.InputHTMLAttributes<HTMLInputElement>,
    "className" | "placeholder" | "size"
  > {
  className?: string;
  description?: React.ReactNode;
  error?: React.ReactNode;
  invalid?: boolean;
  label: string;
  optional?: React.ReactNode;
  required?: boolean;
  requiredIndicator?: React.ReactNode;
  size?: "sm" | "md" | "lg";
  /** Pass-through to the inner <input>. The placeholder is reserved for the
   *  CSS label-float mechanism and cannot be set externally. */
  inputClassName?: string;
}

// ─── Component ───────────────────────────────────────────────────────────────

export const FloatingField = React.forwardRef<
  HTMLInputElement,
  FloatingFieldProps
>(function FloatingField(
  {
    className,
    description,
    disabled,
    error,
    id: idProp,
    inputClassName,
    invalid = false,
    label,
    optional,
    required = false,
    requiredIndicator = "*",
    size = "md",
    ...props
  },
  ref,
) {
  // Generate a stable id if not provided — associates <label> with <input>.
  const autoId = React.useId();
  const id = idProp ?? autoId;

  const styles = floatingFieldVariants({ size });

  // Compose the input's border/focus-ring chrome from the shared L1 variant,
  // but pass surface:"none" — we declare bg-sheet + border directly on the
  // recipe so the floating label background is guaranteed to match.
  const inputChrome = widgetControlSurface({
    surface: "none",
    focus: "none",   // focus handled directly in the recipe (above)
    invalid: false,  // invalid handled via aria-invalid in recipe
    disabled: "pseudo",
  });

  return (
    <div className={cn(styles.root(), className)}>
      <input
        ref={ref}
        id={id}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        aria-required={required || undefined}
        // The single space is the CSS hook. It keeps :placeholder-shown
        // active when the input is empty so the label stays inside.
        placeholder=" "
        className={cn(styles.input(), inputChrome, inputClassName)}
        {...props}
      />
      {/* The label sits on top of the input, positioned via the peer */}
      <label htmlFor={id} className={styles.label()}>
        {label}
        {required && (
          <RequiredMark
            required
            indicator={requiredIndicator}
            className="ml-0.5"
          />
        )}
        {optional && (
          <OptionalHint optional={optional} className="ml-0.5" />
        )}
      </label>

      {/* Helper / error text */}
      {error ? (
        <p className={styles.error()} role="alert">
          {error}
        </p>
      ) : description ? (
        <p className={styles.description()}>{description}</p>
      ) : null}
    </div>
  );
});

FloatingField.displayName = "FloatingField";
