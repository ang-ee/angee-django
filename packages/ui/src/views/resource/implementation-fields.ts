import type { ModelMetadata, Row } from "@angee/metadata";

import { optionToken } from "../../widgets/types";

/** A row's implementation tokens (its `ImplClassField` values), which select `impl` children and variants. */
export function rowImplementations(implFields: readonly string[] | undefined, record: Row | null | undefined): readonly string[] {
  return (implFields ?? []).flatMap((field) => {
    const impl = optionToken(record?.[field]);
    return impl ? [impl] : [];
  });
}

/**
 * Whether `field` reads null because the registry no longer names the implementation key its row
 * stores. An implementation-key column (the resource's `implFields`) is never null in storage, so its
 * read is null only for such a key, which a cell or a field shows as not registered, not as absent.
 * `implFields` names the resource's own columns, so a path through a relation
 * (`channel.backend_class`) is not matched: the cautious side, an empty value as before.
 */
export function isUnregisteredImplementation(
  model: ModelMetadata | null | undefined,
  field: string,
  value: unknown,
): boolean {
  return value == null && Boolean(model?.resource?.implFields?.includes(field));
}

/**
 * Whether the record form's field at `path` (its full form path) is marked not registered: the
 * stored row reads null there for an implementation key the registry no longer names
 * ({@link isUnregisteredImplementation}), and the form's `value` for it is still empty. A create
 * form has no stored row, so it never marks one; picking a registered implementation clears the mark
 * until the draft is discarded. A nested path is never one of `implFields`, so it is never marked.
 */
export function isUnregisteredImplementationField(
  model: ModelMetadata | null | undefined,
  stored: Row | null | undefined,
  path: string,
  value: unknown,
): boolean {
  return stored != null && (value == null || value === "") && isUnregisteredImplementation(model, path, stored[path]);
}
