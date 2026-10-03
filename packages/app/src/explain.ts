import type { CompiledMenus } from "./menus";
import type { ResolvedShell } from "./shell";

/** Why the composed shell and menus look as they do, layer by layer. */
export interface CompositionExplanation {
  shell: Pick<ResolvedShell, "home" | "brand" | "perspective" | "provenance" | "diagnostics">;
  menus: {
    /** The layer that set each node field, declarations included. */
    provenance: CompiledMenus["provenance"];
    removed: CompiledMenus["removed"];
    hidden: CompiledMenus["hidden"];
    /** Console routes a removal made unavailable, with the reason. */
    unavailable: Readonly<Record<string, string>>;
    diagnostics: CompiledMenus["diagnostics"];
  };
}

/** Assemble the composition's explanation from its owners' facts; nothing is re-derived here. */
export function explainComposition(
  shell: ResolvedShell,
  menus: CompiledMenus,
  unavailable: ReadonlyMap<string, string>,
): CompositionExplanation {
  const { home, brand, perspective, provenance, diagnostics } = shell;
  return {
    shell: { ...(home !== undefined ? { home } : {}), brand, perspective, provenance, diagnostics },
    menus: {
      provenance: menus.provenance,
      removed: menus.removed,
      hidden: menus.hidden,
      unavailable: Object.fromEntries([...unavailable].sort(([left], [right]) => left.localeCompare(right))),
      diagnostics: menus.diagnostics,
    },
  };
}
