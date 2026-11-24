import * as React from "react";
import type { FormEventHandler } from "react";

import {
  Dialog,
  type DialogPlacement,
  type DialogSize,
} from "../ui/dialog";
import { FormRoot } from "../ui/form";
import { FormActions, FormGrid } from "../ui/form-layout";
import { useMediaQuery } from "../lib/use-media-query";

export interface DialogFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  onSubmit?: FormEventHandler<HTMLFormElement>;
  size?: DialogSize;
  placement?: DialogPlacement;
  /** Native trigger paired with this dialog for focus restoration. */
  trigger?: React.ReactElement;
}

/** Matches a field as {@link firstField} defines one, an editing host apart. */
const FIELD = [
  "input:not([type='button'], [type='submit'], [type='reset'], [type='image'], [type='hidden'])", "textarea", "select",
  ...["textbox", "searchbox", "combobox", "listbox", "checkbox", "switch", "radio", "slider", "spinbutton"].map((role) => `[role='${role}']`),
].join(", ");

/** An editing host: the element that carries `contenteditable`, as Base UI's tabbable query counts it. */
const EDITING_HOST = "[contenteditable]:not([contenteditable='false'])";

/** Passed over at open: a field a person cannot change now (disabled, natively or with a
 * reason, or read-only) or one in a hidden or inert part. */
const PASSED_OVER = ":disabled, [readonly], [aria-disabled='true'], [aria-readonly='true'], [hidden], [hidden] *, [inert], [inert] *";

/**
 * Whether `element` shows, by the test Base UI's visibility function makes: its `visibility`
 * does not hide it, and `checkVisibility` says so where the platform has it, else the
 * element's own `display` is neither `none` nor `contents`.
 */
function isShown(element: HTMLElement): boolean {
  const styles = getComputedStyle(element);
  if (styles.visibility === "hidden" || styles.visibility === "collapse") return false;
  return typeof element.checkVisibility === "function"
    ? element.checkVisibility()
    : styles.display !== "none" && styles.display !== "contents";
}

/** Whether `element` is a field a person can change now: see {@link firstField}. */
function isOpenField(element: HTMLElement): boolean {
  return (element.matches(EDITING_HOST) || (element.tabIndex >= 0 && element.matches(FIELD)))
    && !element.matches(PASSED_OVER) && isShown(element);
}

/**
 * The first field in `body`, in document order, that a person can change now. A field is a
 * control that holds a value: a native `input` other than a button, image or hidden one, a
 * `textarea` or a `select`, an element with one of ARIA's input roles, or an editing host.
 * A control that only performs an action, a button, a link or a menu trigger, never is one.
 * Base UI does not export its tabbable query, so the platform answers in its place: a field is
 * in the tab order (`tabIndex`; an editing host counts whatever it reads, as in that query),
 * shown by the test of Base UI's visibility function ({@link isShown}), and in a named radio
 * group of one form the checked open radio stands for the group, else its first, as in that
 * query. Unlike that query it does not look for a closed `<details>` around a field, and where
 * the platform lacks `checkVisibility` it does not see a `display: none` ancestor either.
 */
function firstField(body: HTMLElement | null): HTMLElement | null {
  for (const element of body?.querySelectorAll<HTMLElement>("*") ?? []) {
    if (!isOpenField(element)) continue;
    if (element instanceof HTMLInputElement && element.type === "radio" && element.name) {
      return Array.from(body?.querySelectorAll<HTMLInputElement>("input[type='radio']") ?? []).find((radio) =>
        radio.name === element.name && radio.form === element.form && radio.checked && isOpenField(radio)) ?? element;
    }
    return element;
  }
  return null;
}

/**
 * A dialog that collects a form. It opens with focus on its first field, so typing at once
 * fills it. With no field, Base UI's own choice stands: the first tabbable element, the
 * header's Close. Opened by touch on its `trigger`, the dialog focuses itself, as Base UI does
 * for touch, so no on-screen keyboard rises over it. Where the primary pointer is coarse it
 * does the same: that is the platform's answer to a question Base UI cannot ask for a dialog
 * opened through its `open` prop, how it was opened.
 * @kind fragment
 */
export function DialogForm({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  onSubmit,
  size = "sm",
  placement = "prompt",
  trigger,
}: DialogFormProps): React.ReactElement {
  const popupRef = React.useRef<HTMLDivElement>(null);
  const bodyRef = React.useRef<HTMLDivElement>(null);
  const coarsePointer = useMediaQuery("(pointer: coarse)");
  const freshKeySeen = React.useRef(false);
  React.useLayoutEffect(() => {
    if (open) freshKeySeen.current = false;
  }, [open]);
  /** A key already down when the dialog opened does nothing inside its form: until a fresh
   * keydown reaches the form, a repeated keydown and a keyup are stopped before any control in
   * it sees them. */
  const stopOpeningKey = (event: React.KeyboardEvent<HTMLFormElement>): void => {
    if (event.type === "keydown" && !event.repeat) freshKeySeen.current = true;
    else if (!freshKeySeen.current) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {trigger ? <Dialog.Trigger render={trigger} /> : null}
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content
          ref={popupRef}
          placement={placement}
          size={size}
          className="has-[table]:w-[80rem]"
          // `null` hands the choice back to Base UI.
          initialFocus={(openType) => openType === "touch" || coarsePointer ? popupRef.current : firstField(bodyRef.current)}
        >
          {/* The form sits between the height-capped Content and the scrolling
              Body, so it must carry the flex column for the cap to reach Body. */}
          <FormRoot
            layout="plain"
            className="flex min-h-0 flex-col"
            onKeyDownCapture={stopOpeningKey}
            onKeyUpCapture={stopOpeningKey}
            onSubmit={(event) => {
              // The DOM form is portalled, but React still bubbles its submit
              // through the component tree. Keep it from submitting a parent
              // record form; the authored handler owns preventDefault and work.
              event.stopPropagation();
              onSubmit?.(event);
            }}
          >
            <Dialog.Header>
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <Dialog.Title>{title}</Dialog.Title>
                  {description ? (
                    <Dialog.Description>{description}</Dialog.Description>
                  ) : null}
                </div>
                <Dialog.Close />
              </div>
            </Dialog.Header>
            <Dialog.Body ref={bodyRef}>
              <FormGrid>{children}</FormGrid>
            </Dialog.Body>
            {footer ? (
              <Dialog.Footer>
                <FormActions>{footer}</FormActions>
              </Dialog.Footer>
            ) : null}
          </FormRoot>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
