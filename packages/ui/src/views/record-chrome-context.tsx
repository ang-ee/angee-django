import type { Row } from "@angee/metadata";

import { makeContext } from "../runtime";

/**
 * The record a `FORM_VIEW_RECORD_CHROME_SLOT` contribution renders against.
 * `FormView` provides it around the record-chrome slot outlet, so a host/addon
 * chrome contribution self-gates on `resource` and reads the open record's id
 * without re-deriving it from the URL. Present only on a saved record — the slot
 * never renders while creating.
 */
export interface RecordChromeContext {
  /** The model the form renders — a chrome contribution self-gates on it. */
  resource: string;
  /** The open record's public id. */
  recordId: string;
  /** The open record row, or null before it loads. */
  record: Row | null;
}

const binding = makeContext<RecordChromeContext>("RecordChromeContext");

/** Provides the record-chrome context around the record-chrome slot outlet. */
export const RecordChromeProvider = binding.Provider;

/**
 * Read the record-chrome context a `FORM_VIEW_RECORD_CHROME_SLOT` contribution
 * renders within. Throws outside the provider — a contribution always renders
 * inside `FormView`'s record-chrome slot on a saved record.
 */
export const useRecordChromeContext = binding.use;
