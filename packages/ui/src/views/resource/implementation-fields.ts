import type { ModelMetadata, Row } from "@angee/metadata";

import { fieldIsRedacted } from "../form/form-view-model";
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
 * Whether the stored row reads null at `path` for an implementation key the registry no longer names
 * ({@link isUnregisteredImplementation}): the one fact a create-only selector's repair reads. It reads
 * the stored row only, never the form's value, so choosing a key does not change it. A create form
 * has no stored row; a nested path is never one of `implFields`; and a column the record redacts
 * reads null for that reason alone, so none of those holds it.
 */
export function storedImplementationUnregistered(
  model: ModelMetadata | null | undefined,
  stored: Row | null | undefined,
  path: string,
): boolean {
  return stored != null && !fieldIsRedacted(stored, path) && isUnregisteredImplementation(model, path, stored[path]);
}

/**
 * Whether the record form's field at `path` (its full form path) is marked not registered: its stored
 * key is not registered ({@link storedImplementationUnregistered}) and the form's `value` for it is
 * still empty. Picking a registered implementation clears the mark until the draft is discarded.
 */
export function isUnregisteredImplementationField(
  model: ModelMetadata | null | undefined,
  stored: Row | null | undefined,
  path: string,
  value: unknown,
): boolean {
  return (value == null || value === "") && storedImplementationUnregistered(model, stored, path);
}
