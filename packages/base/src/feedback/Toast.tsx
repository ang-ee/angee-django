import {
  useCallback,
  useMemo,
  type ReactElement,
  type ReactNode,
} from "react";
import { Toast as BaseToast } from "@base-ui/react/toast";
import type { ToastObject as BaseToastObject } from "@base-ui/react/toast";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { INTENT_GLYPHS, tones, type FeedbackIntent } from "../lib/tones";
import { Button } from "../ui/button";

export type ToastIntent = "info" | "success" | "warning" | "error";

export interface ToastAction {
  label: ReactNode;
  onClick: () => void;
}

export interface ToastOptions {
  intent?: ToastIntent;
  title: ReactNode;
  description?: ReactNode;
  duration?: number;
  action?: ToastAction;
}

export type ToastShortcutOptions = Omit<ToastOptions, "intent">;

export interface ToastApi {
  (options: ToastOptions): string;
  info: (options: ToastShortcutOptions) => string;
  success: (options: ToastShortcutOptions) => string;
  warning: (options: ToastShortcutOptions) => string;
  error: (options: ToastShortcutOptions) => string;
}

export interface ToastProviderProps {
  children?: ReactNode;
}

interface ToastData {
  intent: ToastIntent;
  action?: ToastAction;
}

const DEFAULT_TOAST_LIMIT = 4;

const DEFAULT_TOAST_DURATIONS: Record<ToastIntent, number> = {
  info: 5000,
  success: 4000,
  warning: 7000,
  error: 0,
};

const TOAST_TONES: Record<ToastIntent, FeedbackIntent> = {
  info: "info",
  success: "success",
  warning: "warning",
  error: "danger",
};

/**
 * Thin wrapper over Base UI's Toast.Provider; Base UI owns the queue, while
 * ModalsHost owns its own confirm queue. The suffix difference is deliberate.
 */
export function ToastProvider({
  children,
}: ToastProviderProps): ReactElement {
  return (
    <BaseToast.Provider limit={DEFAULT_TOAST_LIMIT}>
      {children}
      <ToastViewport />
    </BaseToast.Provider>
  );
}

/** Must be rendered under ToastProvider so Base UI can provide the toast manager. */
export function useToast(): ToastApi {
  const { add } = BaseToast.useToastManager<ToastData>();

  const addToast = useCallback(
    (options: ToastOptions): string => {
      const intent = options.intent ?? "info";
      const toastId = add({
        title: options.title,
        description: options.description,
        type: intent,
        priority: intent === "error" ? "high" : "low",
        timeout: toastTimeout(intent, options.duration),
        data: { intent, action: options.action },
        actionProps: options.action
          ? {
              children: options.action.label,
            }
          : undefined,
      });
      return toastId;
    },
    [add],
  );

  return useMemo<ToastApi>(() => {
    const toast = (options: ToastOptions) => addToast(options);
    return Object.assign(toast, {
      info: (options: ToastShortcutOptions) =>
        addToast({ ...options, intent: "info" }),
      success: (options: ToastShortcutOptions) =>
        addToast({ ...options, intent: "success" }),
      warning: (options: ToastShortcutOptions) =>
        addToast({ ...options, intent: "warning" }),
      error: (options: ToastShortcutOptions) =>
        addToast({ ...options, intent: "error" }),
    });
  }, [addToast]);
}

function ToastViewport(): ReactElement {
  const { toasts } = BaseToast.useToastManager<ToastData>();

  return (
    <BaseToast.Portal>
      <BaseToast.Viewport
        className={cn(
          "pointer-events-none fixed inset-x-4 bottom-4 z-toast h-[var(--toast-frontmost-height)]",
          "sm:bottom-5 sm:left-auto sm:right-5 sm:w-96",
        )}
      >
        {toasts.map((toast) => (
          <ToastItem key={toast.id} toast={toast} />
        ))}
      </BaseToast.Viewport>
    </BaseToast.Portal>
  );
}

function ToastItem({
  toast,
}: {
  toast: BaseToastObject<ToastData>;
}): ReactElement {
  const { close } = BaseToast.useToastManager<ToastData>();
  const t = useBaseT();
  const intent = toast.data?.intent ?? "info";
  const tone = tones[TOAST_TONES[intent]];
  const action = toast.data?.action;

  return (
    <BaseToast.Root
      toast={toast}
      swipeDirection={["right", "down"]}
      className={cn(
        "pointer-events-auto absolute bottom-0 right-0 w-full rounded-md border p-3 shadow-popover outline-none",
        "transition-[opacity,transform,height] duration-200 ease-out focus-visible:focus-ring",
        "translate-x-[var(--toast-swipe-movement-x)] translate-y-[calc((var(--toast-index)*-0.625rem)+var(--toast-swipe-movement-y))]",
        "scale-[calc(1-(var(--toast-index)*0.04))] [z-index:calc(20-var(--toast-index))]",
        "data-[expanded]:translate-y-[calc((var(--toast-offset-y)*-1)+var(--toast-swipe-movement-y))] data-[expanded]:scale-100",
        "data-[ending-style]:opacity-0 data-[limited]:opacity-0 data-[starting-style]:opacity-0",
        tone.bg,
        tone.border,
        tone.fg,
      )}
    >
      <BaseToast.Content className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2">
        <span
          className={cn(
            "mt-0.5 grid size-5 place-content-center [&_.glyph]:size-4",
            tone.fg,
          )}
        >
          <Glyph decorative name={INTENT_GLYPHS[TOAST_TONES[intent]]} />
        </span>
        <div className="min-w-0">
          <BaseToast.Title className="text-13 font-semibold leading-snug" />
          <BaseToast.Description className="mt-1 text-13 leading-snug" />
          <BaseToast.Action
            className="mt-2"
            onClick={() => {
              action?.onClick?.();
              close(toast.id);
            }}
            render={
              <Button type="button" variant="secondary" size="sm" />
            }
          />
        </div>
        <BaseToast.Close
          render={
            <Button
              type="button"
              variant="icon"
              size="iconSm"
              aria-label={t("toast.dismiss")}
              className="text-current hover:bg-sheet/70 hover:text-current"
            >
              <Glyph decorative name="x" />
            </Button>
          }
        />
      </BaseToast.Content>
    </BaseToast.Root>
  );
}

function toastTimeout(intent: ToastIntent, duration: number | undefined): number {
  const resolved = duration ?? DEFAULT_TOAST_DURATIONS[intent];
  return resolved === Infinity ? 0 : resolved;
}
