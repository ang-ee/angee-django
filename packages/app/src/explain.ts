import type { RuntimeComposition } from "@angee/ui/runtime";

import type { CompiledMenus } from "./menus";
import type { ResolvedShell } from "./shell";

/** Why the composed shell and menus look as they do, layer by layer: the facts developer mode shows. */
export interface CompositionExplanation extends RuntimeComposition {
  shell: ResolvedShell;
}

/** Assemble the composition's explanation from its owners' facts; nothing is re-derived here. */
export function explainComposition(
  shell: ResolvedShell,
  menus: CompiledMenus,
  unavailable: ReadonlyMap<string, string>,
  effective: CompositionExplanation["effective"],
): CompositionExplanation {
  const { provenance, removed, hidden, diagnostics } = menus;
  return {
    shell,
    effective,
    menus: { provenance, removed, hidden, unavailable: Object.fromEntries(unavailable), diagnostics },
  };
}
