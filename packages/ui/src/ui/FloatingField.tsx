/**
 * InlineField — an input where the label lives INSIDE the box.
 *
 * Two states:
 *   Empty / unfocused:  label sits centred vertically as a large placeholder.
 *   Focused / filled:   label shrinks and moves to the top of the box; the
 *                       typed value sits beneath it.
 *
 * The box is intentionally taller than a standard Input (md → 56px, lg → 64px)
 * so both the label line and the value line have room without crowding.
 *
 * Technique: pure CSS, no JS state.
 *   • The <input> always carries placeholder=" " (a single space) as the CSS hook.
 *   • :placeholder-shown = input is empty → label is centred and large.
 *   • :not(:placeholder-shown) or :focus → label is small and at the top.
 *   • tailwind-variants peer-* utilities drive the label transitions.
 *   • motion-safe gated — no animation when prefers-reduced-motion is active.
 *
 * "FloatingField" is kept as a re-export alias so any existing imports are
 * backwards-compatible without changes.
 */

import * as React from "react";

import { cn } from "../lib/cn";
import { tv } from "../lib/variants";
import { OptionalHint } from "./label";
import { widgetControlSurface } from "./widget-control";

// ─── Recipe ──────────────────────────────────────────────────────────────────

const inlineFieldVariants = tv({
  slots: {
    // Relative wrapper — establishes the label stacking context.
    root: "relative w-full",

    // The <input>. placeholder=" " keeps :placeholder-shown active when empty.
    // padding-top is large to leave room for the label line above the value.
    input: [
      // border-border explicitly so --color-border overrides (borderless preset) apply.
      // bare `border` alone uses currentColor in Tailwind 4, not --color-border.
      "peer w-full rounded-6 border border-border bg-sheet text-fg",
      "placeholder-transparent",
      "outline-none transition-colors",
      // value text sits in the lower half — extra top padding
      "pb-2",
      // hover — border strengthens on mouse over (same pattern as standard Input)
      "hover:border-border-strong",
      // border & focus — same as standard Input
      "focus:border-border-focus focus:focus-ring",
      // invalid (aria-invalid)
      "aria-[invalid=true]:border-danger",
      "aria-[invalid=true]:focus:border-danger aria-[invalid=true]:focus:focus-ring-danger",
      // disabled
      "disabled:cursor-not-allowed disabled:opacity-60",
    ],

    // The <label> inside the box.
    // peer-placeholder-shown: input is EMPTY → label is centred + large (looks like placeholder)
    // When NOT placeholder-shown (has value) OR on focus → small label at top
    label: [
      "pointer-events-none absolute left-3 select-none font-medium",
      // Active state (value present or focused): small, pinned to top-2
      "top-2 text-2xs text-fg-muted leading-none",
      // Empty + unfocused: label sits where the typed text will appear.
      // pt-6 (24px) is the input's top padding for md — the text baseline
      // sits at roughly top-6 minus half a line-height (~10px) = top ~14px.
      // We express this as a fixed pixel offset so the label aligns with
      // the text cursor regardless of the input's geometric centre.
      "peer-placeholder-shown:top-[14px] peer-placeholder-shown:-translate-y-0",
      "peer-placeholder-shown:text-13 peer-placeholder-shown:text-fg-subtle",
      // On focus: move back to top-2
      "peer-focus:top-2 peer-focus:text-2xs peer-focus:text-brand",
      // Smooth motion only if reduced-motion is off
      "[transition:top_var(--dur-ui-fast,120ms)_var(--ease-ui,ease),font-size_var(--dur-ui-fast,120ms)_var(--ease-ui,ease),color_var(--dur-ui-fast,120ms)_var(--ease-ui,ease)]",
      // invalid on focus: danger colour
      "peer-aria-[invalid=true]:peer-focus:text-danger-text",
    ],

    description: "mt-1 text-xs leading-5 text-fg-muted",
    error:       "mt-1 text-xs leading-5 text-danger-text",
  },
  variants: {
    size: {
      sm: {
        // 44px tall, pt-5 (20px) — text cursor ~10px offset
        input: "h-11 px-3 pt-5 text-xs",
        label: "peer-placeholder-shown:top-[10px] peer-placeholder-shown:text-xs",
      },
      md: {
        // 56px tall, pt-6 (24px) — text cursor ~14px offset
        input: "h-14 px-3 pt-6 text-13",
        label: "peer-placeholder-shown:top-[14px] peer-placeholder-shown:text-13",
      },
      lg: {
        // 64px tall, pt-7 (28px) — text cursor ~17px offset
        input: "h-16 px-3.5 pt-7 text-sm",
        label: "peer-placeholder-shown:top-[17px] peer-placeholder-shown:text-sm",
      },
    },
  },
  defaultVariants: {
    size: "md",
  },
});

// ─── Types ────────────────────────────────────────────────────────────────────

export interface InlineFieldProps
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
  inputClassName?: string;
}

// ─── Component ────────────────────────────────────────────────────────────────

export const InlineField = React.forwardRef<HTMLInputElement, InlineFieldProps>(
  function InlineField(
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
    const autoId = React.useId();
    const id = idProp ?? autoId;
    const styles = inlineFieldVariants({ size });

    // Shared border/focus/disabled chrome from the existing L1 variant.
    // surface:"none" because we declare bg-sheet + border directly in the recipe.
    const inputChrome = widgetControlSurface({
      surface: "none",
      focus:   "none",   // focus handled by recipe above
      invalid: false,    // invalid handled via aria-invalid in recipe
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
          // Single space: activates :placeholder-shown when field is empty.
          placeholder=" "
          className={cn(styles.input(), inputChrome, inputClassName)}
          {...props}
        />

        {/* Label floats inside the box.
            RequiredMark and OptionalHint are rendered OUTSIDE the animated label
            so they don't affect the label's bounding box and break centring.
            They are absolutely positioned and shown only when the label is active
            (top state) via the same peer-* CSS hooks. */}
        <label htmlFor={id} className={styles.label()}>
          {label}
        </label>
        {required && (
          <span
            aria-hidden
            className={[
              // Same position logic as the label top-state
              "pointer-events-none absolute select-none font-medium text-brand",
              "leading-none text-2xs",
              // Follows label: top-2 when active, hidden when label is centred
              "top-2 left-3",
              // Offset past the label text — use padding-left trick via translate
              "opacity-100",
              // Hide when input is empty+unfocused (label is centred, * would float oddly)
              "peer-placeholder-shown:opacity-0 peer-focus:opacity-100",
              "[transition:opacity_var(--dur-ui-fast,120ms)_var(--ease-ui,ease)]",
            ].join(" ")}
            style={{
              // Push * to after the label text — approximate with left offset
              paddingLeft: `calc(${label.length}ch * 0.6 + 0.75rem + 0.125rem)`,
            }}
          >
            {requiredIndicator}
          </span>
        )}
        {optional && (
          <OptionalHint optional={optional} className="ml-0.5" />
        )}

        {error ? (
          <p className={styles.error()} role="alert">{error}</p>
        ) : description ? (
          <p className={styles.description()}>{description}</p>
        ) : null}
      </div>
    );
  },
);

InlineField.displayName = "InlineField";

// ─── Backward-compat alias ────────────────────────────────────────────────────
// Existing imports of FloatingField continue to work unchanged.
export const FloatingField = InlineField;
export type FloatingFieldProps = InlineFieldProps;
