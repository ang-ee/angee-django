import { optionToken } from "../../widgets/types";
import {
  FORM_VIEW_RECORD_ACTIONS_SLOT,
  FORM_VIEW_SECTIONS_SLOT,
} from "../../runtime/contracts";
import type { ModelSlotTarget } from "../../runtime";

/** Passive record chrome rendered at the right edge of a saved form toolbar. */
export const FORM_VIEW_RECORD_CHROME_SLOT = "form-view.record-chrome";

export { FORM_VIEW_RECORD_ACTIONS_SLOT, FORM_VIEW_SECTIONS_SLOT };

/** Resolve the model/implementation-scoped record-action contribution target. */
export function formViewRecordActionsSlot(
  resource: string,
  implValue?: string,
): ModelSlotTarget {
  if (implValue === undefined) {
    return { slot: FORM_VIEW_RECORD_ACTIONS_SLOT, model: resource };
  }
  const impl = optionToken(implValue);
  if (!impl) {
    throw new Error(
      `Record-verb slot for "${resource}" was given an impl value that carries no ` +
        `key (${JSON.stringify(implValue)}); pass the ImplClassField key, or omit ` +
        "it for the model-scoped target.",
    );
  }
  return { slot: FORM_VIEW_RECORD_ACTIONS_SLOT, model: resource, impl };
}

/** Resolve the model-scoped contributed-form-section target. */
export function formViewSectionsSlot(resource: string): ModelSlotTarget {
  return { slot: FORM_VIEW_SECTIONS_SLOT, model: resource };
}
