import type { DataResourceMetadata, SchemaFieldMetadata } from "./artifact";
import { modelLabelSegment } from "./naming";

/**
 * Merge the resource inventories emitted for several normalized GraphQL
 * schemas. Repeated resources are retained: the same canonical label may be
 * exposed by several schemas, and the resolver treats that repetition as one
 * candidate while preserving each schema's resource facts for consumers that
 * need them.
 */
export function mergeModelLabelInventory(
  metadatas: readonly SchemaFieldMetadata[],
): readonly DataResourceMetadata[] {
  return metadatas.flatMap((metadata) => metadata.resources ?? []);
}

/**
 * Resolve an authored model spelling to its emitted canonical `modelLabel`.
 *
 * Accepted spellings are the exact qualified label (`integrate.Integration`),
 * the label's bare model segment (`Integration`), and the emitted lowercase
 * `modelName` (`integration`). Exact qualified labels always win. Bare and
 * lowercase aliases must identify one canonical label across the supplied
 * resource inventory; repeating the same label in several GraphQL schemas is harmless,
 * while aliases shared by different labels are ambiguous and throw. Unknown
 * spellings throw as well, so registry declarations cannot silently miss.
 */
export function canonicalModelLabel(
  resources: readonly DataResourceMetadata[],
  spelling: string,
): string {
  if (resources.length === 0) {
    throw new Error(
      `Cannot resolve model spelling "${spelling}": schema metadata exposes no resources.`,
    );
  }
  const exact = resources.find((resource) => resource.modelLabel === spelling);
  if (exact) return exact.modelLabel;

  const candidates = new Set<string>();
  for (const resource of resources) {
    const modelSegment = modelLabelSegment(resource.modelLabel);
    if (
      spelling === modelSegment
      || spelling === modelSegment.toLowerCase()
      || spelling === resource.modelName
    ) {
      candidates.add(resource.modelLabel);
    }
  }
  if (candidates.size === 1) return [...candidates][0]!;
  if (candidates.size > 1) {
    throw new Error(
      `Model spelling "${spelling}" is ambiguous; it matches ${
        [...candidates].sort().map((label) => `"${label}"`).join(", ")
      }. Use a qualified model label.`,
    );
  }
  throw new Error(
    `Unknown model spelling "${spelling}"; declare a model exposed in schema metadata.`,
  );
}

/**
 * Render-safe form of {@link canonicalModelLabel}. Unknown and ambiguous
 * spellings produce no label and warn in development instead of throwing from
 * a React render.
 */
export function canonicalModelLabelOrNull(
  resources: readonly DataResourceMetadata[],
  spelling: string,
  context: string,
): string | null {
  if (!spelling) return null;
  try {
    return canonicalModelLabel(resources, spelling);
  } catch (error) {
    if (developmentMode()) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[Angee] ${context}: ${message}`);
    }
    return null;
  }
}

function developmentMode(): boolean {
  const viteEnv = (
    import.meta as ImportMeta & { readonly env?: { readonly DEV?: boolean } }
  ).env;
  if (typeof viteEnv?.DEV === "boolean") return viteEnv.DEV;
  const nodeEnv = (
    globalThis as typeof globalThis & {
      process?: { env?: { NODE_ENV?: string } };
    }
  ).process?.env?.NODE_ENV;
  return nodeEnv !== "production";
}
