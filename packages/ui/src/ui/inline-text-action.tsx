import * as React from "react";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { Button, type ButtonSize, type ButtonVariant } from "./button";
import { FormRoot } from "./form";
import { Input, type InputSize } from "./input";

export interface InlineTextActionControls {
  busy: boolean;
  editing: boolean;
  open: () => void;
}

export interface InlineTextActionProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "children" | "onSubmit"> {
  /** The current value when editing an existing item; omit for create flows. */
  value?: string;
  /** Controlled edit state. */
  open?: boolean;
  /** Initial uncontrolled edit state. */
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Receives the already-trimmed, non-empty value. */
  onSubmit: (value: string) => void;
  onCancel?: () => void;
  /** Accessible name for the text input. */
  inputLabel: string;
  placeholder?: string;
  submitLabel?: React.ReactNode;
  requiredMessage?: React.ReactNode;
  /** External action/mutation state. */
  busy?: boolean;
  disabled?: boolean;
  /** Defaults to false so a rename submit with no actual change just cancels. */
  submitUnchanged?: boolean;
  selectOnFocus?: boolean;
  renderTrigger?: (controls: InlineTextActionControls) => React.ReactNode;
  formClassName?: string;
  inputClassName?: string;
  inputSize?: InputSize;
  submitSize?: ButtonSize;
  submitVariant?: ButtonVariant;
}

/**
 * Inline create/rename owner: one trimmed text value, Enter submit, Escape/blur
 * cancel, focus management, busy state, and accessible labels. Addons provide
 * domain labels/icons; this primitive owns the interaction contract.
 */
export function InlineTextAction({
  value = "",
  open,
  defaultOpen = false,
  onOpenChange,
  onSubmit,
  onCancel,
  inputLabel,
  placeholder,
  submitLabel,
  requiredMessage,
  busy = false,
  disabled = false,
  submitUnchanged = false,
  selectOnFocus = true,
  renderTrigger,
  className,
  formClassName,
  inputClassName,
  inputSize = "sm",
  submitSize = "sm",
  submitVariant = "secondary",
  ...props
}: InlineTextActionProps): React.ReactElement {
  const t = useBaseT();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen);
  const editing = open ?? uncontrolledOpen;
  const [draft, setDraft] = React.useState(value);
  const [showRequired, setShowRequired] = React.useState(false);
  const messageId = React.useId();

  const trimmed = draft.trim();
  const original = value.trim();
  const unchanged = trimmed === original;
  const canSubmit =
    !disabled &&
    !busy &&
    trimmed.length > 0 &&
    (submitUnchanged || !unchanged);
  const invalid = showRequired && trimmed.length === 0;

  const setEditing = React.useCallback(
    (next: boolean) => {
      if (open === undefined) setUncontrolledOpen(next);
      onOpenChange?.(next);
    },
    [onOpenChange, open],
  );

  const cancel = React.useCallback(() => {
    setDraft(value);
    setShowRequired(false);
    setEditing(false);
    onCancel?.();
  }, [onCancel, setEditing, value]);

  const start = React.useCallback(() => {
    if (disabled || busy) return;
    setDraft(value);
    setShowRequired(false);
    setEditing(true);
  }, [busy, disabled, setEditing, value]);

  React.useLayoutEffect(() => {
    if (!editing) return;
    const input = inputRef.current;
    input?.focus();
    if (selectOnFocus) input?.select();
  }, [editing, selectOnFocus]);

  React.useEffect(() => {
    if (!editing) setDraft(value);
  }, [editing, value]);

  const controls = React.useMemo<InlineTextActionControls>(
    () => ({ busy, editing, open: start }),
    [busy, editing, start],
  );

  if (!editing) {
    return (
      <div className={cn("min-w-0", className)} {...props}>
        {renderTrigger?.(controls) ?? null}
      </div>
    );
  }

  return (
    <div className={cn("min-w-0", className)} {...props}>
      <FormRoot
        layout="inline"
        density="compact"
        aria-busy={busy || undefined}
        aria-label={inputLabel}
        className={cn("items-center gap-1", formClassName)}
        onBlur={(event) => {
          const nextTarget = event.relatedTarget;
          if (nextTarget && event.currentTarget.contains(nextTarget)) return;
          if (!busy) cancel();
        }}
        onFormSubmit={() => {
          if (disabled || busy) return;
          if (!trimmed) {
            setShowRequired(true);
            inputRef.current?.focus();
            return;
          }
          if (!submitUnchanged && unchanged) {
            cancel();
            return;
          }
          onSubmit(trimmed);
          setDraft("");
          setShowRequired(false);
          setEditing(false);
        }}
      >
        <Input
          ref={inputRef}
          size={inputSize}
          value={draft}
          placeholder={placeholder}
          disabled={disabled || busy}
          invalid={invalid}
          aria-label={inputLabel}
          aria-describedby={invalid ? messageId : undefined}
          className={inputClassName}
          onChange={(event) => {
            setDraft(event.currentTarget.value);
            if (showRequired) setShowRequired(false);
          }}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            cancel();
          }}
        />
        <Button
          type="submit"
          size={submitSize}
          variant={submitVariant}
          loading={busy}
          disabled={!canSubmit}
        >
          {submitLabel ?? t("inlineText.submit")}
        </Button>
        {invalid ? (
          <span id={messageId} className="sr-only">
            {requiredMessage ?? t("inlineText.required")}
          </span>
        ) : null}
      </FormRoot>
    </div>
  );
}
