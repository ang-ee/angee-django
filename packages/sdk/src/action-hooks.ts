import { useCallback, useMemo } from "react";

import { type ActionOutcome, type ByIdVariables, runActionResult } from "./action-result";
import { useDocumentMutation } from "./document-mutation";

/** The PascalCase operation name for a camelCase action field. */
function actionDocument(field: string): string {
  const op = `${field[0]!.toUpperCase()}${field.slice(1)}`;
  return `mutation ${op}($id: ID!) { ${field}(id: $id) { ok message } }`;
}

export type ActionMutate = (id: string) => Promise<string | undefined>;

/**
 * Run a single-id action mutation — `<field>(id: ID!): ActionResult`
 * — derived from its field name alone: no authored document, result type, or
 * variables. The runner applies {@link runActionResult} (a business failure
 * throws → error toast; success returns its message → success toast), so it drops
 * straight into a record `<Action>` handler.
 *
 * `TField` defaults to `string`; pin it to the generated `ActionFieldName` union
 * (`@angee/gql/<schema>/actions`) for a compile-time check that the field is a
 * real action mutation in that schema:
 *
 * ```ts
 * import type { ActionFieldName } from "@angee/gql/console/actions";
 * const [provision, state] = useActionMutation<ActionFieldName>("provisionAgent");
 * ```
 */
export function useActionMutation<TField extends string = string>(
  field: TField,
): [ActionMutate, { fetching: boolean; error: Error | null }] {
  const document = useMemo(() => actionDocument(field), [field]);
  const { execute, fetching, error } = useDocumentMutation<
    Record<string, ActionOutcome>,
    ByIdVariables
  >(document);
  const run = useCallback<ActionMutate>(
    async (id) => runActionResult((await execute({ id }))?.[field]),
    [execute, field],
  );
  return [run, { fetching, error }];
}
