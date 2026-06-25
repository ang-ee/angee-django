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
import { INTENT_GLYPHS, toneClass, type FeedbackIntent } from "../lib/tones";
import { Button } from "../ui/button";

/** Toasts speak in the feedback tones (negative state is `danger`, not `error`). */
export type ToastTone = FeedbackIntent;

export interface ToastAction {
  label: ReactNode;
  onClick: () => void;
}

export interface ToastOptions {
  tone?: ToastTone;
  title: ReactNode;
  description?: ReactNode;
  duration?: number;
  action?: ToastAction;
}

export type ToastShortcutOptions = Omit<ToastOptions, "tone">;

export interface ToastApi {
  (options: ToastOptions): string;
  info: (options: ToastShortcutOptions) => string;
  success: (options: ToastShortcutOptions) => string;
  warning: (options: ToastShortcutOptions) => string;
  danger: (options: ToastShortcutOptions) => string;
  close: (id?: string) => void;
}

export interface ToastProviderProps {
  children?: ReactNode;
}

interface ToastData {
  tone: ToastTone;
  action?: ToastAction;
}

const DEFAULT_TOAST_LIMIT = 4;

const DEFAULT_TOAST_DURATIONS: Record<ToastTone, number> = {
  info: 5000,
  success: 4000,
  warning: 7000,
  danger: 0,
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
  const { add, close } = BaseToast.useToastManager<ToastData>();

  const addToast = useCallback(
    (options: ToastOptions): string => {
      const tone = options.tone ?? "info";
      const toastId = add({
        title: options.title,
        description: options.description,
        type: tone,
        priority: tone === "danger" ? "high" : "low",
        timeout: toastTimeout(tone, options.duration),
        data: { tone, action: options.action },
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
        addToast({ ...options, tone: "info" }),
      success: (options: ToastShortcutOptions) =>
        addToast({ ...options, tone: "success" }),
      warning: (options: ToastShortcutOptions) =>
        addToast({ ...options, tone: "warning" }),
      danger: (options: ToastShortcutOptions) =>
        addToast({ ...options, tone: "danger" }),
      close,
    });
  }, [addToast, close]);
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
  const tone = toast.data?.tone ?? "info";
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
        toneClass(tone, "soft"),
      )}
    >
      <BaseToast.Content className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2">
        <span className="mt-0.5 grid size-5 place-content-center [&_.glyph]:size-4">
          <Glyph decorative name={INTENT_GLYPHS[tone]} />
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

function toastTimeout(tone: ToastTone, duration: number | undefined): number {
  const resolved = duration ?? DEFAULT_TOAST_DURATIONS[tone];
  return resolved === Infinity ? 0 : resolved;
}
