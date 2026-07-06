import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import type { ActionOutcome } from "@angee/refine";

import { errorMessage, useToast } from "../feedback";
import { useUiT } from "../i18n";
import { useResourceRoute } from "../runtime";
import { recordPath } from "./resource-routing";

/** Django's non-field key: in-band reasons a preflight surfaces at form level. */
const NON_FIELD_ERRORS = "__all__";

export interface ActionResultRunOptions {
  /**
   * Model label whose routed collection page a returned `id` deep-links into
   * (e.g. `"inventory.Transfer"`). Resolved through the runtime's
   * `routesByResource`; when the model has no routed page — or the outcome
   * carries no `id` — the run only toasts.
   */
  linkTo?: string;
  /** Toast title when the verb resolves without an outcome payload. */
  noResultTitle?: string;
}

/**
 * Fire one `ActionResult` verb and settle its outcome. Resolves the outcome
 * (or `undefined` on a missing payload / thrown transport error) so a caller
 * can chain domain follow-ups without re-deriving the feedback.
 */
export type ActionResultRun = (
  fire: () => Promise<ActionOutcome | null | undefined>,
) => Promise<ActionOutcome | undefined>;

/**
 * The settle owner for `ActionResult` verbs: fire the mutation, toast the
 * outcome, and deep-link to the record the verb created.
 *
 * - a missing payload or a thrown error → a danger toast (`noResultTitle`,
 *   defaulting to the ui bundle's `action.noResult`; a thrown error's message
 *   wins when it carries one);
 * - `ok=false` → a danger toast titled by the verb's `message`, with any
 *   in-band non-field reasons (`validationErrors.__all__` — the shape the
 *   backend action preflight raises) as its description;
 * - `ok=true` → a success toast, then — when the outcome carries the created
 *   record's `id` and `linkTo` resolves a routed resource page — navigation to
 *   that record's detail.
 *
 * Serves authored verbs (extract the outcome field inside `fire`, e.g.
 * `(await generate({ order: id }))?.generate_delivery`) and derived single-id
 * verbs (`useActionMutation`'s mutate already resolves the outcome) alike.
 * The mutation hook keeps owning cache invalidation; refresh work belongs in
 * the `fire` closure.
 */
export function useActionResultRun(
  options: ActionResultRunOptions = {},
): ActionResultRun {
  const t = useUiT();
  const toast = useToast();
  const navigate = useNavigate();
  const targetPath = useResourceRoute(options.linkTo ?? "");
  const noResultTitle = options.noResultTitle;
  return React.useCallback<ActionResultRun>(
    async (fire) => {
      const fallbackTitle = noResultTitle ?? t("action.noResult");
      let outcome: ActionOutcome | null | undefined;
      try {
        outcome = await fire();
      } catch (error) {
        toast.danger({ title: errorMessage(error, fallbackTitle) });
        return undefined;
      }
      if (!outcome) {
        toast.danger({ title: fallbackTitle });
        return undefined;
      }
      if (!outcome.ok) {
        const reasons = outcome.validationErrors?.[NON_FIELD_ERRORS]?.join(" ");
        toast.danger({
          title: outcome.message,
          ...(reasons ? { description: reasons } : {}),
        });
        return outcome;
      }
      toast.success({ title: outcome.message });
      if (outcome.id && targetPath) {
        void navigate({ to: recordPath(targetPath, outcome.id) });
      }
      return outcome;
    },
    [navigate, noResultTitle, t, targetPath, toast],
  );
}
