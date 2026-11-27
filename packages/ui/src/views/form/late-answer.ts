import * as React from "react";
import { failedWriteOutcome } from "@angee/refine";

import { errorMessage, useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import type { FormSubmitResult } from "./validation-errors";

/** A submit's answer: the result its owner returned, or what it threw. */
export type LateAnswer = FormSubmitResult<unknown> | { thrown: unknown };

/**
 * Report a submit's answer that arrived after its surface went away (unmounted, or closed and
 * opened anew), so nobody believes a write went out, or did not, unaware. Call the returned
 * function with the answer (the result the submit returned, or `{ thrown }` with what it threw)
 * and `fallback`, the surface's own sentence for a failed write, which titles the toast. An
 * accepted result toasts its message, when it has one; a refused one, its reasons; a thrown
 * failure, its reason, and for a lost answer that it may not have been sent. A request the
 * connection boundary refused before sending is that boundary's to report, so it toasts
 * nothing. Report only an answer whose surface is gone: a surface still shown tells its own.
 * Both form dialogs report through it.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
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
