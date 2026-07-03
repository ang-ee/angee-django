import { useCallback, useMemo, useRef } from "react";
import type {
  NotificationProvider,
  OpenNotificationParams,
} from "@refinedev/core";

import { useUiT } from "../i18n";
import { useToast, type ToastTone } from "./Toast";

const REFINE_NOTIFICATION_TONE: Readonly<
  Record<OpenNotificationParams["type"], ToastTone>
> = {
  success: "success",
  error: "danger",
  progress: "info",
};

export function useRefineNotificationProvider(): NotificationProvider {
  const toast = useToast();
  const t = useUiT();
  const idsByKey = useRef(new Map<string, string>());

  const close = useCallback(
    (key: string) => {
      const id = idsByKey.current.get(key) ?? key;
      toast.close(id);
      idsByKey.current.delete(key);
    },
    [toast],
  );

  return useMemo(
    () => ({
      open(params) {
        if (params.key) close(params.key);
        const id = toast({
          tone: REFINE_NOTIFICATION_TONE[params.type],
          title: params.message,
          description: params.description,
          duration: durationFor(params),
          action: params.cancelMutation
            ? {
                label: t("toast.cancel"),
                onClick: params.cancelMutation,
              }
            : undefined,
        });
        if (params.key) idsByKey.current.set(params.key, id);
      },
      close,
    }),
    [close, t, toast],
  );
}

function durationFor(params: OpenNotificationParams): number | undefined {
  if (params.type === "progress") return 0;
  return params.undoableTimeout ? params.undoableTimeout * 1000 : undefined;
}
