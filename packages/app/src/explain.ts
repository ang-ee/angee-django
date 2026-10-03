import type { ComposedContainers, ContainerCondition, RuntimeComposition } from "@angee/ui/runtime";

import type { CompiledMenus } from "./menus";
import type { ResolvedShell } from "./shell";

/** Why the composed shell, menus and containers look as they do, layer by layer: the facts developer mode shows. */
export interface CompositionExplanation extends RuntimeComposition {
  shell: ResolvedShell;
}

/** Assemble the composition's explanation from its owners' facts; nothing is re-derived here. */
export function explainComposition(
  shell: ResolvedShell,
  menus: CompiledMenus,
  unavailable: ReadonlyMap<string, string>,
  effective: CompositionExplanation["effective"],
  containers?: ComposedContainers,
): CompositionExplanation {
  const { provenance, removed, hidden, diagnostics } = menus;
  return {
    shell,
    effective,
    menus: { provenance, removed, hidden, unavailable: Object.fromEntries(unavailable), diagnostics },
    ...(containers ? {
      containers: {
        removed: containers.removed,
        rules: Object.entries(containers.rules).flatMap(([address, rules]) =>
          rules.map((rule) => ({ address, layer: rule.layer, summary: ruleSummary(rule) }))),
        provenance: containers.provenance,
        diagnostics: containers.diagnostics,
      },
    } : {}),
  };
}

function ruleSummary(rule: ComposedContainers["rules"][string][number]): string {
  const parts = [
    rule.only ? `${rule.force ? "force only" : "only"} [${rule.only.join(", ")}]` : undefined,
    rule.except ? `except [${rule.except.join(", ")}]` : undefined,
    rule.hide ? `hide [${rule.hide.join(", ")}]` : undefined,
    rule.show ? `show [${rule.show.join(", ")}]` : undefined,
    rule.when ? `when ${conditionText(rule.when)}` : undefined,
  ];
  return parts.filter(Boolean).join(" ");
}

function conditionText(condition: ContainerCondition): string {
  return Object.entries(condition).map(([key, value]) =>
    `${key} ${typeof value === "string" ? value : (value as readonly string[]).join("|")}`).join(", ");
}
