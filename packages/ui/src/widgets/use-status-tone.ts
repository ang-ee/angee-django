import { useCallback } from "react";
import type { Tone } from "../lib/tones";
import { useAppRuntime } from "../runtime/runtime";
import { statusTone, type StatusToneOptions } from "./status-tones";

/** Resolve status tones against the current app's composed vocabulary. */
export function useStatusTone(): (
  value: string | null | undefined,
  override?: Record<string, Tone>,
  options?: Omit<StatusToneOptions, "statusTones">,
) => Tone {
  const { statusTones } = useAppRuntime();
  return useCallback(
    (value, override, options = {}) => statusTone(value, override, { ...options, statusTones }),
    [statusTones],
  );
}
