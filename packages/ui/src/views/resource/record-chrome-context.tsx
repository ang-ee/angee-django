import type { ReactElement } from "react";
import type { Row } from "@angee/metadata";

import { useMemo } from "react";

import { makeContext, useContainer, modelChain } from "../../runtime";
import { ContainerOutlet } from "../../lib/container-outlet";

/**
 * The record a `form#actions`, `form#actions-menu` or `form#chrome` child renders
 * against. `FormView` provides it around those containers, so a child reads the
 * open record and its id without re-deriving either from the URL. Present only on
 * a saved record; none of them renders while creating.
 *
 * A child at a model address already knows its model, and an `impl` child or a
 * variant already knows the row's implementation, so it gates only on what its
 * address has not decided (typically lifecycle). A `form#chrome` child shows on
 * every form, so it still gates on `resource` itself.
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
  /** Whether the owning form is read-only. */
  formReadOnly: boolean;
  /** Dirty or pending saved form; independent record verbs must wait. */
  actionsBlocked?: boolean;
}

const binding = makeContext<RecordChromeContext>("RecordChromeContext");

/** Provides the record-chrome context around the record containers. */
export const RecordChromeProvider = binding.Provider;

/**
 * Read the saved-record toolbar context. Throws outside the provider — a
 * child always renders inside one of `FormView`'s record toolbar containers.
 */
export const useRecordChromeContext = binding.use;

/** Read an enclosing saved record from a nested collection, when present. */
export const useRecordChromeContextMaybe = binding.useMaybe;

/** Shared outlet for saved forms and custom record surfaces: the `form#chrome` children for this record's models. */
export function RecordChrome({ value }: { value: RecordChromeContext }): ReactElement {
  const models = useMemo(
    () => modelChain(value.canonicalResource, value.resource),
    [value.canonicalResource, value.resource],
  );
  const entries = useContainer("form#chrome", { models, row: value.record });
  return (
    <RecordChromeProvider value={value}>
      <ContainerOutlet entries={entries} />
    </RecordChromeProvider>
  );
}
