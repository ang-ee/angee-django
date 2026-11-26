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
