import type { ModelMetadata } from "@angee/metadata";

/**
 * The one placeholder for a value that is absent in a cell or a read-only
 * field: an em dash, never a hyphen, a blank or copy of its own.
 * @kind token
 */
export const EMPTY_VALUE = "—";

/**
 * Whether `field` reads null because the registry no longer names the implementation key its row
 * stores. An implementation-key column (the resource's `implFields`) is never null in storage, so
 * its read is null only for such a key; a cell or a field shows it as not registered, not as absent.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function isUnregisteredImplementation(
  model: ModelMetadata | null | undefined,
  field: string,
  value: unknown,
): boolean {
  return value == null && Boolean(model?.resource?.implFields?.includes(field));
}
