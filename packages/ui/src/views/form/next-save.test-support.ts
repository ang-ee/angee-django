import { expect } from "vitest";
import type { FormNextSave, FormViewSaveSurface } from "./use-form-view-save";

/**
 * A record form's `nextSave()` read twice, as a pane reads it: the same value both times; the reads leave
 * the values, the baseline, the dirty and touched fields, the errors, the submit count and the `requests`
 * made as they were; and what they return shares nothing with the form, so writing into it changes
 * nothing there. Returns a copy of the read, to compare with what the writer then receives.
 */
export function readNextSave(surface: () => FormViewSaveSurface, requests: () => number): FormNextSave {
  const state = () => {
    const { form } = surface();
    return {
      values: structuredClone(form.getValues()), baseline: structuredClone(form.formState.defaultValues),
      dirtyFields: structuredClone(form.formState.dirtyFields), touchedFields: structuredClone(form.formState.touchedFields),
      errors: Object.keys(form.formState.errors), submits: form.formState.submitCount, requests: requests(),
    };
  };
  const before = state();
  const next = surface().nextSave();
  expect(surface().nextSave()).toEqual(next);
  expect(state()).toEqual(before);
  const read = structuredClone(next);
  scribble(next);
  expect(state()).toEqual(before);
  return read;
}

/** Write into every object and list `value` holds. */
function scribble(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(scribble);
    value.push("scribbled");
    return;
  }
  if (value === null || typeof value !== "object") return;
  for (const child of Object.values(value)) scribble(child);
  (value as Record<string, unknown>).scribbled = true;
}
