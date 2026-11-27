import * as React from "react";
import type { FormEventHandler } from "react";

import {
  Dialog,
  type DialogPlacement,
  type DialogSize,
} from "../ui/dialog";
import { Button } from "../ui/button";
import { FormRoot } from "../ui/form";
import { FormActions, FormGrid } from "../ui/form-layout";
import { useUiT } from "../i18n";
import { useLatestRef } from "../lib/use-latest-ref";
import { useMediaQuery } from "../lib/use-media-query";
import { useUnsavedChangesNavigationGuard } from "../views/form/use-unsaved-changes-navigation-guard";

/** What a {@link DialogForm}'s footer function gets: the dialog's own dismissal, for a Cancel. */
export interface DialogFormFooterContext {
  /** Dismiss as Escape does: refused while `pending`, asked first while the form holds work. */
  dismiss: () => void;
  /** The reason a Cancel gives while a submission runs (its `disabledReason`), else null. */
  dismissRefusal: string | null;
}

export interface DialogFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
  /** The form's buttons. As a function it gets the dialog's own dismissal, for a Cancel that
   *  follows `pending` and `dirty` ({@link DialogFormFooterContext}). */
  footer?: React.ReactNode | ((dialog: DialogFormFooterContext) => React.ReactNode);
  onSubmit?: FormEventHandler<HTMLFormElement>;
  size?: DialogSize;
  placement?: DialogPlacement;
  /** Native trigger paired with this dialog for focus restoration. */
  trigger?: React.ReactElement;
  /** A submission runs: every dismissal (Escape, the backdrop, Close, `dismiss`) is refused
   *  until it settles, so its answer shows in the dialog; the dialog is busy, Close and the
   *  footer's Cancel say why, and a refused Escape or backdrop press is announced. */
  pending?: boolean;
  /** The form holds typed content. Given at all (true or false), the dialog guards its work
   *  from the moment it opens, so it renders under the app's router and `ModalsHost`: a dismissal
   *  of typed content asks first, with the shared unsaved-changes question, and a route move asks
   *  while it holds typed content or a running submission. A close the owner makes itself through
   *  `open` (the accepted close after a submit) is no dismissal. Opened without the router it
   *  throws at render `useUnsavedChangesNavigationGuard must be used under the app's router.`;
   *  without `ModalsHost`, `useConfirm must be used under ModalsHost.` A test or a story provides
   *  both through the shared providers (`createUiTestProviders` or `AppShellTestProviders` with
   *  `router`, the workshop's routed fixtures). */
  dirty?: boolean;
  /** Whether the dialog holds work a route move or a dismissal would lose, read live when one
   *  is asked; by default `pending || dirty` as last rendered. An owner whose accepted submit
   *  closes the dialog and moves on in the same tick says no there. */
  holdsWork?: () => boolean;
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
 * opened through its `open` prop, how it was opened. While `pending` it refuses to be
 * dismissed, and given `dirty` it guards typed content and a running submission against a
 * dismissal and a route move (`dirty`), so it renders open under the app's router and
 * `ModalsHost`, and throws without them, naming the one missing.
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
  pending = false,
  dirty,
  holdsWork,
}: DialogFormProps): React.ReactElement {
  const t = useUiT();
  const dismissRefusal = pending ? t("dialog.pendingDismiss") : null;
  const latest = useLatestRef({ onOpenChange, pending, dirty, holdsWork, dismissRefusal });
  const requestLeave = React.useRef<(() => Promise<boolean>) | null>(null);
  const asking = React.useRef(false);
  const [refusal, setRefusal] = React.useState<string | null>(null);
  React.useEffect(() => { if (!pending) setRefusal(null); }, [pending]);
  const holdsWorkNow = React.useCallback(() => {
    const { holdsWork, pending, dirty } = latest.current;
    return holdsWork ? holdsWork() : pending || dirty === true;
  }, [latest]);
  const pendingNow = React.useCallback(() => latest.current.pending, [latest]);
  const dismiss = React.useCallback(() => {
    if (latest.current.pending) {
      setRefusal(latest.current.dismissRefusal);
      return;
    }
    // One question at a time: Escape or a press outside reaching this dialog while it asks is not another dismissal.
    if (asking.current) return;
    const request = requestLeave.current;
    if (!request || !holdsWorkNow()) {
      latest.current.onOpenChange(false);
      return;
    }
    asking.current = true;
    void request().then((leave) => {
      if (leave) latest.current.onOpenChange(false);
    }).finally(() => { asking.current = false; });
  }, [holdsWorkNow, latest]);
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
    <Dialog.Root open={open} onOpenChange={(next) => { if (next) onOpenChange(true); else dismiss(); }}>
      {open && dirty !== undefined ? <WorkGuard holding={pending || dirty} holdsWork={holdsWorkNow}
        pendingNow={pendingNow} requestLeave={requestLeave} /> : null}
      {trigger ? <Dialog.Trigger render={trigger} /> : null}
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content
          ref={popupRef}
          placement={placement}
          size={size}
          className="has-[table]:w-[80rem]"
          aria-busy={pending || undefined}
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
                {/* Focusable while refused, so a keyboard user keeps their place; the reason is its description. */}
                <Dialog.Close render={<Button variant="ghost" size="iconSm" disabledReason={dismissRefusal} />} />
              </div>
            </Dialog.Header>
            <Dialog.Body ref={bodyRef}>
              <FormGrid>{children}</FormGrid>
            </Dialog.Body>
            <span role="status" className="sr-only">{refusal}</span>
            {footer ? (
              <Dialog.Footer>
                <FormActions>{typeof footer === "function" ? footer({ dismiss, dismissRefusal }) : footer}</FormActions>
              </Dialog.Footer>
            ) : null}
          </FormRoot>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** The guard of an open dialog that holds work: a route move asks while it does, and a dismissal
 * asks the same question (`requestLeave`). Mounted while open, so a missing router or
 * `ModalsHost` is said when the dialog opens, not at the first keystroke. */
function WorkGuard({ holding, holdsWork, pendingNow, requestLeave }: {
  holding: boolean;
  holdsWork: () => boolean;
  pendingNow: () => boolean;
  requestLeave: React.MutableRefObject<(() => Promise<boolean>) | null>;
}): null {
  const request = useUnsavedChangesNavigationGuard({ isDirty: holding, isDirtyNow: holdsWork, readOnly: false, pendingNow });
  React.useEffect(() => {
    requestLeave.current = request;
    return () => { requestLeave.current = null; };
  }, [request, requestLeave]);
  return null;
}
