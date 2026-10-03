import type { CompiledMenus } from "./menus";
import type { ResolvedShell } from "./shell";

/** Why the composed shell and menus look as they do, layer by layer. */
export interface CompositionExplanation {
  /** The shell the layers resolved, with its provenance and fallback diagnostics. */
  shell: ResolvedShell;
  /** What the app runs with once deprecated `createApp` inputs override the shell. */
  effective: { home: string; confineTo: string | null };
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
  effective: CompositionExplanation["effective"],
): CompositionExplanation {
  const { provenance, removed, hidden, diagnostics } = menus;
  return {
    shell,
    effective,
    menus: { provenance, removed, hidden, unavailable: Object.fromEntries(unavailable), diagnostics },
  };
}
