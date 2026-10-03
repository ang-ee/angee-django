/**
 * The deployment's `ANGEE_UI` layer. The composed runtime appends it after every
 * addon and makes it depend on all of them, so it applies last.
 */
export const DEPLOYMENT_LAYER_ID = "deployment";

/** A composed manifest as a layer: its id and the ids of the manifests it depends on. */
export interface Layer {
  id: string;
  /** Ids of the manifests this one depends on, as the composed runtime supplies them. */
  dependsOn?: readonly string[];
}

/** Each layer's transitive dependencies; an undeclared dependency id fails composition. */
export function layerAncestry(layers: readonly Layer[]): Map<string, Set<string>> {
  const direct = new Map(layers.map((layer) => [layer.id, layer.dependsOn ?? []] as const));
  for (const [id, dependencies] of direct) {
    for (const dependency of dependencies) {
      if (!direct.has(dependency)) throw new Error(`Addon "${id}" depends on unknown addon "${dependency}".`);
    }
  }
  const closures = new Map<string, Set<string>>();
  const visit = (id: string, path: readonly string[]): Set<string> => {
    const known = closures.get(id);
    if (known) return known;
    if (path.includes(id)) throw new Error(`Addon dependencies form a cycle: ${[...path, id].join(" -> ")}.`);
    const reached = new Set<string>();
    for (const dependency of direct.get(id) ?? []) {
      reached.add(dependency);
      for (const ancestor of visit(dependency, [...path, id])) reached.add(ancestor);
    }
    closures.set(id, reached);
    return reached;
  };
  for (const id of direct.keys()) visit(id, []);
  return closures;
}

/** The candidates no other candidate depends on, in input order. */
export function mostSpecific(
  candidates: readonly string[],
  ancestors: ReadonlyMap<string, ReadonlySet<string>>,
): string[] {
  return candidates.filter((id) => !candidates.some((other) => other !== id && ancestors.get(other)?.has(id)));
}

/** Refuse an alteration of something owned by an addon the altering layer does not depend on. */
export function assertMayAlter(
  ancestors: ReadonlyMap<string, ReadonlySet<string>>,
  layer: string,
  owner: string,
  what: string,
): void {
  if (owner !== layer && !ancestors.get(layer)?.has(owner)) {
    throw new Error(`Addon "${layer}" alters ${what} of "${owner}", which it does not depend on.`);
  }
}

/**
 * Whether `layer` may set a field `previous` already set: the same layer or a
 * dependent overrides; a dependency setting it after its dependent leaves the
 * dependent's value (returns false); two unrelated layers fail.
 */
export function overridesField(
  ancestors: ReadonlyMap<string, ReadonlySet<string>>,
  previous: string | undefined,
  layer: string,
  what: string,
): boolean {
  if (previous === undefined || previous === layer || ancestors.get(layer)?.has(previous)) return true;
  if (ancestors.get(previous)?.has(layer)) return false;
  throw new Error(`Unrelated addons "${previous}" and "${layer}" both set ${what}.`);
}
