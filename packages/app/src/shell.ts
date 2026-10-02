import type { RuntimeBrand } from "@angee/ui/runtime";

/** Shell facts a product addon declares; a dependent overrides its dependencies field by field. */
export interface ShellDeclaration {
  /** Where `/` lands: a route name or an absolute path. */
  home?: string;
  brand?: RuntimeBrand;
  /** The selected perspective id; `null` keeps the full console. */
  perspective?: string | null;
}

/** A named confinement: the console shows one menu root and redirects other console routes home. */
export interface PerspectiveDeclaration {
  /** The menu root the rail, palette and route guard are confined to. */
  root: string;
  /** Where `/` lands under this perspective; defaults to the shell's home. */
  home?: string;
}

/** The layer facts `resolveShell` reads from each composed manifest. */
export interface ShellLayer {
  id: string;
  /** Ids of the manifests this one depends on, as the composed runtime supplies them. */
  dependsOn?: readonly string[];
  shell?: ShellDeclaration;
  /** @deprecated Declare `shell.brand`. */
  brand?: RuntimeBrand;
  perspectives?: Readonly<Record<string, PerspectiveDeclaration>>;
}

export interface ResolvedShell {
  home?: string;
  brand: RuntimeBrand | null;
  perspective: ({ id: string } & PerspectiveDeclaration) | null;
  /** The layer that supplied each resolved field. */
  provenance: Readonly<Partial<Record<keyof ShellDeclaration, string>>>;
  /** Why a declared field fell back to the framework default. */
  diagnostics: readonly string[];
}

/** The deployment's `ANGEE_UI` layer, appended by the composed runtime after every addon. */
export const DEPLOYMENT_LAYER_ID = "deployment";

const SHELL_FIELDS = ["home", "brand", "perspective"] as const;

/**
 * Resolve home, brand and perspective from the composed layers.
 *
 * One product is selected atomically: among the addons declaring a shell, the
 * one no other declarer depends on. Its own declaration and its ancestors'
 * merge field by field, the nearest declarer winning. Unrelated products, or
 * unrelated ancestors of the selected product setting one field, fall back to
 * the framework default instead of failing; the deployment layer applies last.
 */
export function resolveShell(layers: readonly ShellLayer[]): ResolvedShell {
  const ids = new Set(layers.map((layer) => layer.id));
  const ancestors = ancestry(layers, ids);
  const perspectives = new Map<string, PerspectiveDeclaration>();
  for (const layer of layers) {
    for (const [id, perspective] of Object.entries(layer.perspectives ?? {})) {
      if (perspectives.has(id)) throw new Error(`Addon "${layer.id}" redefines perspective "${id}".`);
      if (!perspective.root?.trim()) throw new Error(`Perspective "${id}" of addon "${layer.id}" declares no menu root.`);
      perspectives.set(id, perspective);
    }
  }
  const declarations = new Map<string, ShellDeclaration>();
  for (const layer of layers) {
    if (layer.brand && layer.shell?.brand) {
      throw new Error(`Addon "${layer.id}" declares both brand and shell.brand; keep shell.brand.`);
    }
    const shell = layer.brand ? { ...layer.shell, brand: layer.brand } : layer.shell;
    if (shell?.brand && (!shell.brand.name.trim() || !shell.brand.mark.trim())) {
      throw new Error(`Addon "${layer.id}" declares an empty brand name or mark.`);
    }
    if (shell && Object.keys(shell).length) declarations.set(layer.id, shell);
  }

  const resolved: { [K in keyof ShellDeclaration]?: ShellDeclaration[K] } = {};
  const provenance: Partial<Record<keyof ShellDeclaration, string>> = {};
  const diagnostics: string[] = [];
  const deployment = declarations.get(DEPLOYMENT_LAYER_ID);
  declarations.delete(DEPLOYMENT_LAYER_ID);
  const products = mostSpecific([...declarations.keys()], ancestors);
  if (products.length > 1) {
    diagnostics.push(`Unrelated products ${products.join(", ")} declare a shell; pin one in ANGEE_UI.`);
  } else if (products.length === 1) {
    const product = products[0]!;
    const chain = [...declarations.keys()].filter((id) => id === product || ancestors.get(product)!.has(id));
    for (const field of SHELL_FIELDS) {
      const winners = mostSpecific(chain.filter((id) => declarations.get(id)![field] !== undefined), ancestors);
      if (winners.length > 1) {
        diagnostics.push(`Unrelated layers ${winners.join(", ")} set shell.${field}; ${product} or ANGEE_UI decides.`);
      } else if (winners.length === 1) {
        assign(resolved, field, declarations.get(winners[0]!)![field]);
        provenance[field] = winners[0]!;
      }
    }
  }
  for (const field of SHELL_FIELDS) {
    if (deployment?.[field] === undefined) continue;
    assign(resolved, field, deployment[field]);
    provenance[field] = DEPLOYMENT_LAYER_ID;
  }

  let perspective: ResolvedShell["perspective"] = null;
  if (typeof resolved.perspective === "string") {
    const declared = perspectives.get(resolved.perspective);
    if (!declared) throw new Error(`Shell selects unknown perspective "${resolved.perspective}".`);
    perspective = { id: resolved.perspective, ...declared };
  }
  const home = deployment?.home ?? perspective?.home ?? resolved.home;
  if (home !== undefined && deployment?.home === undefined && perspective?.home !== undefined) {
    provenance.home = provenance.perspective!;
  }
  return {
    ...(home !== undefined ? { home } : {}),
    brand: resolved.brand ?? null,
    perspective,
    provenance,
    diagnostics,
  };
}

function assign<K extends keyof ShellDeclaration>(
  target: { [F in keyof ShellDeclaration]?: ShellDeclaration[F] },
  field: K,
  value: ShellDeclaration[K],
): void {
  target[field] = value;
}

/** Each layer's transitive dependencies; an undeclared dependency id fails composition. */
function ancestry(layers: readonly ShellLayer[], ids: ReadonlySet<string>): Map<string, Set<string>> {
  const direct = new Map(layers.map((layer) => [layer.id, layer.dependsOn ?? []] as const));
  for (const [id, dependencies] of direct) {
    for (const dependency of dependencies) {
      if (!ids.has(dependency)) throw new Error(`Addon "${id}" depends on unknown addon "${dependency}".`);
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
function mostSpecific(candidates: readonly string[], ancestors: ReadonlyMap<string, ReadonlySet<string>>): string[] {
  return candidates.filter((id) => !candidates.some((other) => other !== id && ancestors.get(other)?.has(id)));
}
