import type { ReactElement } from "react";
import type { Row } from "@angee/metadata";

import { makeContext, useSlot } from "../../runtime";
import { SlotOutlet } from "../../lib/slot-outlet";
import { FORM_VIEW_RECORD_CHROME_SLOT } from "../form/form-view-slots";

/**
 * The record a `formViewRecordActionsSlot(...)` or `FORM_VIEW_RECORD_CHROME_SLOT`
 * contribution renders against. `FormView` provides it around both outlets, so a
 * contribution reads the open record and its id without re-deriving either from
 * the URL. Present only on a saved record — neither slot renders while creating.
 *
 * A record-verb slot key already settles the model, and the impl key settles the
 * row's `ImplClassField` value too, so a contribution there gates only on what
 * its key has not already decided (typically lifecycle) — never by re-probing the
 * record for the model or impl its key named. The chrome slot is global, so a
 * chrome contribution still gates on `resource` itself.
 */
export interface RecordChromeContext {
  /** The model the form renders — a global chrome contribution gates on it. */
  resource: string;
  /** The schema-named data provider that owns this record. */
  dataProviderName: string | undefined;
  /** Canonical MTI resource label, falling back to `resource`. */
  canonicalResource: string;
  /** The open record's public id. */
  recordId: string;
  /** The open record row, or null before it loads. */
  record: Row | null;
}

const binding = makeContext<RecordChromeContext>("RecordChromeContext");

/** Provides the record-chrome context around the record-chrome slot outlet. */
export const RecordChromeProvider = binding.Provider;

/**
 * Read the saved-record toolbar context. Throws outside the provider — a
 * contribution always renders inside one of `FormView`'s record toolbar slots.
 */
export const useRecordChromeContext = binding.use;

/** Read an enclosing saved record from a nested collection, when present. */
export const useRecordChromeContextMaybe = binding.useMaybe;

/** Shared outlet for saved forms and custom record surfaces. */
export function RecordChrome({ value }: { value: RecordChromeContext }): ReactElement {
  const entries = useSlot(FORM_VIEW_RECORD_CHROME_SLOT);
  return (
    <RecordChromeProvider value={value}>
      <SlotOutlet entries={entries} />
    </RecordChromeProvider>
  );
}
