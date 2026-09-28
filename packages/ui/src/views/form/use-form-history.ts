import * as React from "react";
import { stableSerialize } from "@angee/refine";
import type { FieldValues, UseFormReturn } from "react-hook-form";

export interface FormHistory {
  canUndo: boolean;
  canRedo: boolean;
  /** Record synchronous changes; return false without running when read-only. */
  perform: (action: () => void) => boolean;
  /** Begin one undo group, for example when a text field receives focus. */
  start: (group: string) => void;
  /** Finish the matching group, for example when that field loses focus. */
  commit: (group: string) => void;
  undo: () => void;
  redo: () => void;
  /** Clear snapshots after loading another record or accepting a saved baseline. */
  reset: () => void;
}

/**
 * Explicit undo groups for JSON-like form values. React Hook Form remains the
 * value/default owner; replay uses its reset lifecycle and retains its baseline.
 * Bind start(group) before the first input change and commit(group) on blur.
 * Repeated starts in that group coalesce typing into one undo step. Starting a
 * different group commits the previous one. Undo/redo preserve the active group
 * so continued typing without a new focus event remains undoable. Wrap other
 * synchronous changes in perform; asynchronous work must finish before that call.
 * Each changed RHF notification creates one immutable frame, reused by stacks
 * and availability checks. Both stacks retain at most limit frames (default 100).
 * Call reset when the caller loads another record or accepts a saved baseline.
 */
export function useFormHistory<TValues extends FieldValues>(
  form: Pick<UseFormReturn<TValues>, "getValues" | "reset" | "subscribe">,
  { readOnly = false, limit = 100 }: { readOnly?: boolean; limit?: number } = {},
): FormHistory {
  if (!Number.isSafeInteger(limit) || limit < 1) throw new RangeError("History limit must be a positive integer.");
  type Frame = { values: TValues; key: string };
  const { getValues, reset: resetForm, subscribe } = form;
  const framesRef = React.useRef<{
    current: Frame; past: Frame[]; future: Frame[];
    active: { group: string; before: Frame } | null;
  } | null>(null);
  if (framesRef.current === null) {
    const values = getValues();
    framesRef.current = {
      current: { values: structuredClone(values), key: stableSerialize(values) },
      past: [], future: [], active: null,
    };
  }
  const frames = framesRef.current;
  const [, redraw] = React.useReducer((value: number) => value + 1, 0);
  const availability = React.useSyncExternalStore(
    React.useCallback((notify) => {
      const update = (values: TValues) => {
        const key = stableSerialize(values);
        if (key !== frames.current.key) {
          frames.current = { values: structuredClone(values), key };
          notify();
        }
      };
      const unsubscribe = subscribe({ formState: { values: true }, callback: ({ values }) => update(values) });
      update(getValues());
      return unsubscribe;
    }, [frames, getValues, subscribe]),
    React.useCallback(() => {
      const changed = frames.active !== null && frames.active.before.key !== frames.current.key;
      return (frames.past.length > 0 || changed ? 1 : 0) | (!changed && frames.future.length > 0 ? 2 : 0);
    }, [frames]),
    () => 0,
  );
  const push = React.useCallback((stack: Frame[], frame: Frame) => {
    stack.push(frame);
    if (stack.length > limit) stack.splice(0, stack.length - limit);
  }, [limit]);
  const record = React.useCallback((before: Frame) => {
    if (before.key === frames.current.key) return;
    push(frames.past, before);
    frames.future = [];
    redraw();
  }, [frames, push]);
  const finish = React.useCallback(() => {
    const pending = frames.active;
    if (!pending) return;
    frames.active = null;
    record(pending.before);
  }, [frames, record]);
  const perform = React.useCallback((action: () => void) => {
    if (readOnly) return false;
    const group = frames.active?.group;
    finish();
    const before = frames.current;
    try { action(); } finally {
      record(before);
      frames.active = group === undefined ? null : { group, before: frames.current };
    }
    return true;
  }, [frames, finish, readOnly, record]);
  const start = React.useCallback((group: string) => {
    if (readOnly || frames.active?.group === group) return;
    finish();
    frames.active = { group, before: frames.current };
  }, [frames, finish, readOnly]);
  const commit = React.useCallback((group: string) => {
    if (frames.active?.group === group) finish();
  }, [frames, finish]);
  const replay = React.useCallback((direction: "undo" | "redo") => {
    const group = frames.active?.group;
    finish();
    const source = direction === "undo" ? frames.past : frames.future;
    const destination = direction === "undo" ? frames.future : frames.past;
    const frame = source.pop();
    // Focus has not moved: later typing must still belong to this input group.
    frames.active = group === undefined ? null : { group, before: frame ?? frames.current };
    if (!frame) return;
    push(destination, frames.current);
    frames.current = frame;
    // RHF clones reset values; the immutable history frame is safe to reuse.
    resetForm(frame.values, { keepDefaultValues: true });
    redraw();
  }, [frames, finish, push, resetForm]);
  const undo = React.useCallback(() => {
    if (readOnly) return;
    replay("undo");
  }, [readOnly, replay]);
  const redo = React.useCallback(() => {
    if (readOnly) return;
    replay("redo");
  }, [readOnly, replay]);
  const reset = React.useCallback(() => {
    frames.past = [];
    frames.future = [];
    frames.active = null;
    redraw();
  }, [frames]);
  return {
    canUndo: !readOnly && Boolean(availability & 1),
    canRedo: !readOnly && Boolean(availability & 2),
    perform, start, commit, undo, redo, reset,
  };
}
