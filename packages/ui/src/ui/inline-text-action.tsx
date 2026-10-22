import * as React from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Button, type ButtonSize, type ButtonVariant } from "./button";
import { FormRoot } from "./form";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { Input, type InputSize } from "./input";
import { Textarea } from "./textarea";

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
  /**
   * Receives the already-trimmed, non-empty value. A returned promise keeps the
   * editor open and busy until it settles; `false` or a rejection keeps it open
   * (the caller shows the failure through `error`).
   */
  onSubmit: (value: string) => void | boolean | Promise<void | boolean>;
  onCancel?: () => void;
  /**
   * A multi-line body: a textarea where Enter breaks the line and ⌘/Ctrl+Enter
   * submits; a visible Cancel replaces blur-cancel so a long draft never vanishes
   * on a stray click.
   */
  multiline?: boolean;
  /** The textarea's visible rows. */
  rows?: number;
  /** Controls rendered before the verbs (a visibility picker). */
  extra?: React.ReactNode;
  /** The failure under the editor after a rejected submit. */
  error?: React.ReactNode;
  cancelLabel?: React.ReactNode;
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
 * @kind primitive
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
  busy: externalBusy = false,
  disabled = false,
  submitUnchanged = false,
  selectOnFocus = true,
  multiline = false,
  rows = 3,
  extra,
  error,
  cancelLabel,
  renderTrigger,
  className,
  formClassName,
  inputClassName,
  inputSize = "sm",
  submitSize = "sm",
  submitVariant = "secondary",
  ...props
}: InlineTextActionProps): React.ReactElement {
  const t = useUiT();
  const inputRef = React.useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen);
  const editing = open ?? uncontrolledOpen;
  const [draft, setDraft] = React.useState(value);
  const [showRequired, setShowRequired] = React.useState(false);
  const [settling, setSettling] = React.useState(false);
  const busy = externalBusy || settling;
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
  const errorId = `${messageId}-error`;

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
        className={cn(multiline ? "flex-wrap items-center gap-2" : "items-center gap-1", formClassName)}
        onBlur={(event) => {
          if (multiline) return;
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
          const settled = (accepted: boolean | void) => {
            if (accepted === false) return;
            setDraft("");
            setShowRequired(false);
            setEditing(false);
          };
          const result = onSubmit(trimmed);
          if (result instanceof Promise) {
            setSettling(true);
            void result.then(settled, () => undefined).finally(() => setSettling(false));
            return;
          }
          settled(result);
        }}
      >
        {multiline ? (
          <Textarea
            ref={inputRef}
            rows={rows}
            value={draft}
            placeholder={placeholder}
            disabled={disabled || busy}
            invalid={invalid}
            aria-label={inputLabel}
            aria-describedby={invalid ? messageId : error ? errorId : undefined}
            className={cn("w-full", inputClassName)}
            onChange={(event) => {
              setDraft(event.currentTarget.value);
              if (showRequired) setShowRequired(false);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                cancel();
              } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
        ) : (
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
        )}
        {extra}
        <Button
          type="submit"
          size={submitSize}
          variant={submitVariant}
          loading={busy}
          disabled={!canSubmit}
        >
          {submitLabel ?? t("inlineText.submit")}
        </Button>
        {multiline ? (
          <Button type="button" size={submitSize} variant="ghost" disabled={busy} onClick={cancel}>
            {cancelLabel ?? t("inlineText.cancel")}
          </Button>
        ) : null}
        {invalid ? (
          <span id={messageId} className="sr-only">
            {requiredMessage ?? t("inlineText.required")}
          </span>
        ) : null}
        {error ? <ErrorBanner id={errorId} description={error} className="basis-full" /> : null}
      </FormRoot>
    </div>
  );
}
