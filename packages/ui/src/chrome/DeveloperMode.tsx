import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { useAppRuntime, useDeveloperMode, useRuntimeComposition, type RuntimeComposition } from "../runtime";
import { Banner } from "../ui/alert";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Glyph } from "./Glyph";
import type { ChromeMenuNode } from "./menu-tree";

/** The user-menu switch for developer mode. */
export function DeveloperModeMenuItem(): ReactElement {
  const t = useUiT();
  const { enabled, setEnabled } = useDeveloperMode();
  return (
    <DropdownMenu.CheckboxItem inset={false} checked={enabled} onCheckedChange={(checked) => setEnabled(checked)}>
      <Glyph name="code-xml" />
      <span className="flex-1 truncate">{t("developer.mode")}</span>
      {enabled ? <Glyph name="check" /> : null}
    </DropdownMenu.CheckboxItem>
  );
}

/** The console notice developer mode adds: the page's route and app, and the composition's findings. */
export function DeveloperPanel(): ReactElement | null {
  const t = useUiT();
  const { enabled } = useDeveloperMode();
  const runtime = useAppRuntime();
  const composition = useRuntimeComposition();
  if (!enabled) return null;
  const perspective = composition?.shell.perspective;
  const facts = [
    [t("developer.route"), runtime.activeRoute ?? "—"],
    [t("developer.app"), runtime.activeApp ?? "—"],
    [t("developer.perspective"), perspective ? `${perspective.id} (${perspective.root})` : t("developer.none")],
    [t("developer.home"), composition?.effective.home ?? "—"],
  ] as const;
  return (
    <Banner tone="info" title={t("developer.title")}>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-xs">
        {facts.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-fg-muted">{label}</dt>
            <dd className="min-w-0 truncate">{value}</dd>
          </div>
        ))}
      </dl>
      {composition ? <CompositionDetails composition={composition} /> : null}
    </Banner>
  );
}

function CompositionDetails({ composition }: { composition: RuntimeComposition }): ReactElement {
  const t = useUiT();
  const { shell, menus } = composition;
  const sections: readonly (readonly [string, readonly string[]])[] = [
    [t("developer.shell"), Object.entries(shell.provenance).map(([field, layer]) => `${field} ← ${layer}`)],
    [t("developer.removed"), menus.removed.map((node) => `${node.id} ← ${node.by}`)],
    [t("developer.hidden"), menus.hidden.map((node) => `${node.id} ← ${node.by} (${node.reason})`)],
    [t("developer.unavailable"), Object.entries(menus.unavailable).map(([route, reason]) => `${route}: ${reason}`)],
    [t("developer.diagnostics"), [...shell.diagnostics, ...menus.diagnostics]],
  ];
  return (
    <details className="mt-1 text-xs">
      <summary className="cursor-pointer">{t("developer.composition")}</summary>
      {sections.filter(([, lines]) => lines.length).map(([title, lines]) => (
        <section key={title} className="mt-1">
          <h3 className="font-semibold">{title}</h3>
          <ul className="font-mono">
            {lines.map((line) => <li key={line} className="truncate">{line}</li>)}
          </ul>
        </section>
      ))}
    </details>
  );
}

/**
 * What the rail shows in developer mode: hidden children too, and each item's id
 * and the layers that shaped it; removed items sit where they were.
 */
export function useDeveloperRail(): {
  enabled: boolean;
  children: (item: ChromeMenuNode) => readonly ChromeMenuNode[];
  describe: (item: ChromeMenuNode) => string | undefined;
  removedUnder: (parentId: string) => RuntimeComposition["menus"]["removed"];
} {
  const t = useUiT();
  const { enabled } = useDeveloperMode();
  const composition = useRuntimeComposition();
  return {
    enabled,
    children: (item) => (enabled ? (item.children ?? []).filter((child) => child.target) : item.targetedChildren),
    describe: (item) => {
      if (!enabled) return undefined;
      const provenance = composition?.menus.provenance[item.id] ?? {};
      const hidden = composition?.menus.hidden.find((entry) => entry.id === item.id);
      const layers = [...new Set(Object.values(provenance))];
      return [item.id, layers.length ? `← ${layers.join(", ")}` : undefined,
        hidden ? t("developer.hiddenBy", { layer: hidden.by, reason: hidden.reason }) : undefined]
        .filter(Boolean).join(" · ");
    },
    removedUnder: (parentId) => (enabled ? composition?.menus.removed.filter((node) => node.parent === parentId) ?? [] : []),
  };
}
