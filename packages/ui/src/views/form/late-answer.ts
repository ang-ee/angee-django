import * as React from "react";
import { failedWriteOutcome } from "@angee/refine";

import { errorMessage, useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import type { FormSubmitResult } from "./validation-errors";

/** A submit's answer: the result its owner returned, or what it threw. */
export type LateAnswer = FormSubmitResult<unknown> | { thrown: unknown };

/**
 * Report a submit's answer that arrived after its form went away (unmounted, or closed and
 * opened anew), so nobody believes a write went out, or did not, unaware: an accepted result
 * toasts its message; a refused one, its reasons; a failure, its reason, and for a lost
 * answer that it may not have been sent. A request the connection boundary refused before
 * sending is that boundary's to report.
 */
export function useLateAnswerReport(): (answer: LateAnswer, fallback: string) => void {
  const toast = useToast();
  const t = useUiT();
  return React.useCallback((answer, fallback) => {
    if ("thrown" in answer) {
      const outcome = failedWriteOutcome(answer.thrown);
      if (outcome === "not-sent") return;
      toast.danger({
        title: fallback,
        description: outcome === "unconfirmed" ? t("form.answerLost") : errorMessage(answer.thrown, fallback),
      });
      return;
    }
    if (answer.status === "ok") {
      if (answer.message) toast.success({ title: answer.message });
      return;
    }
    const reasons = answer.status === "conflict"
      ? [answer.message]
      : [...answer.issues.formErrors, ...Object.values(answer.issues.fieldErrors).flat()];
    toast.danger({ title: fallback, description: reasons.join("\n") || undefined });
  }, [t, toast]);
}
