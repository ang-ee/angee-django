import * as React from "react";

import type {
  MutationDialogProps,
  MutationDialogValues,
} from "./views/form/MutationDialog";

export type MutationDialogTestDoubleProps = MutationDialogProps<
  Record<string, unknown>,
  unknown
> &
  Record<string, unknown>;

export interface MutationDialogTestDoubleOptions {
  capture?: (props: MutationDialogTestDoubleProps) => void;
  values?:
    | MutationDialogValues
    | ((props: MutationDialogTestDoubleProps) => MutationDialogValues);
  submitLabel?:
    | React.ReactNode
    | ((props: MutationDialogTestDoubleProps) => React.ReactNode);
}

/**
 * Minimal shared test rendering for consumers that own MutationDialog
 * declarations rather than its interaction ceremony. Callers spread the real
 * `@angee/ui` module in their mock, so codecs and every unrelated UI owner stay
 * production-accurate.
 */
export function createMutationDialogTestDouble({
  capture,
  values = {},
  submitLabel = "Submit mutation dialog",
}: MutationDialogTestDoubleOptions = {}): (
  props: MutationDialogTestDoubleProps,
) => React.ReactElement | null {
  return function MutationDialogTestDouble(
    props: MutationDialogTestDoubleProps,
  ): React.ReactElement | null {
    capture?.(props);
    if (!props.open) return null;

    const buttonLabel =
      typeof submitLabel === "function" ? submitLabel(props) : submitLabel;

    return (
      <form
        aria-label={String(props.title)}
        onSubmit={(event) => {
          event.preventDefault();
          const rawValues =
            typeof values === "function" ? values(props) : values;
          const parsed = props.parseValues({
            ...props.initialValues,
            ...rawValues,
          });
          void Promise.resolve(props.onSubmit(parsed)).then((result) => {
            props.onSubmitted?.(result, parsed);
          });
        }}
      >
        <button type="submit">{buttonLabel}</button>
      </form>
    );
  };
}
