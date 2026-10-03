import { useCallback, useMemo, useState, type ReactElement } from "react";

import { useUiT } from "../i18n";
import {
  useAppRuntime,
  useDeveloperMode,
  useDeveloperModeSwitch,
  useRuntimeComposition,
  type RemovedMenuItem,
  type RuntimeComposition,
} from "../runtime";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Tooltip } from "../ui/tooltip";
import { Glyph } from "./Glyph";
import type { ChromeMenuNode } from "./menu-tree";

/** The user-menu switch for developer mode. */
export function DeveloperModeMenuItem(): ReactElement {
  const t = useUiT();
  const { enabled, setEnabled } = useDeveloperModeSwitch();
  return (
    <DropdownMenu.CheckboxItem inset={false} checked={enabled} onCheckedChange={(checked) => setEnabled(checked)}>
      <Glyph name="code-xml" />
      <span className="flex-1 truncate">{t("developer.mode")}</span>
      {enabled ? <Glyph name="check" /> : null}
    </DropdownMenu.CheckboxItem>
  );
}

/**
 * The top-bar developer button, shown only in developer mode: hovering gives the
 * page's route, app, perspective and home; clicking opens the composition.
 */
export function DeveloperMenu(): ReactElement | null {
  const t = useUiT();
  const enabled = useDeveloperMode();
  const [open, setOpen] = useState(false);
  if (!enabled) return null;
  return (
    <>
      <Tooltip label={<DeveloperSummary />} side="bottom" align="end">
        <Button
          type="button"
          variant="icon"
          size="iconSm"
          aria-label={t("developer.composition")}
          aria-haspopup="dialog"
          onClick={() => setOpen(true)}
          className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
        >
          <Glyph name="code-xml" />
        </Button>
      </Tooltip>
      {open ? <CompositionDialog onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function DeveloperSummary(): ReactElement {
  const t = useUiT();
  const runtime = useAppRuntime();
  const composition = useRuntimeComposition();
  const perspective = composition?.shell.perspective;
  const none = t("developer.none");
  const facts = [
    [t("developer.route"), runtime.activeRouteName ?? none],
    [t("developer.app"), runtime.activeApp ?? none],
    [t("developer.perspective"), perspective ? t("developer.perspectiveValue", { id: perspective.id, root: perspective.root }) : none],
    [t("developer.home"), composition?.effective.home ?? none],
  ] as const;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono">
      {facts.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="opacity-70">{label}</dt>
          <dd className="min-w-0 truncate">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CompositionDialog({ onClose }: { onClose: () => void }): ReactElement {
  const t = useUiT();
  const composition = useRuntimeComposition();
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg" placement="center">
          <Dialog.Header>
            <div className="flex items-start gap-3">
              <Dialog.Title className="min-w-0 flex-1">{t("developer.composition")}</Dialog.Title>
              <Dialog.Close />
            </div>
          </Dialog.Header>
          <Dialog.Body className="space-y-3 text-xs">
            <DeveloperSummary />
            {composition ? <CompositionSections composition={composition} /> : <p>{t("developer.noComposition")}</p>}
          </Dialog.Body>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const layersOf = (fields: Readonly<Record<string, string | undefined>>): string =>
  [...new Set(Object.values(fields).filter((layer) => layer !== undefined))].join(", ");

function CompositionSections({ composition }: { composition: RuntimeComposition }): ReactElement {
  const t = useUiT();
  const { shell, menus } = composition;
  const sections: readonly (readonly [string, readonly string[]])[] = [
    [t("developer.shell"), Object.entries(shell.provenance).flatMap(([field, layer]) =>
      layer === undefined ? [] : [t("developer.setBy", { subject: field, layers: layer })])],
    [t("developer.removed"), menus.removed.map((node) => t("developer.setBy", { subject: node.id, layers: node.by }))],
    [t("developer.hidden"), menus.hidden.map((node) =>
      t("developer.hiddenLine", { id: node.id, layer: node.by, reason: t(`developer.reason.${node.reason}`) }))],
    [t("developer.unavailable"), Object.entries(menus.unavailable).map(([route, reason]) => t("developer.unavailableLine", { route, reason }))],
    // Composition findings are the app's own English diagnostics, shown as raised.
    [t("developer.diagnostics"), [...shell.diagnostics, ...menus.diagnostics]],
    [t("developer.menus"), Object.entries(menus.provenance).map(([id, fields]) => t("developer.setBy", { subject: id, layers: layersOf(fields) }))],
  ];
  return (
    <>
      {sections.filter(([, lines]) => lines.length).map(([title, lines]) => (
        <section key={title}>
          <h3 className="font-semibold">{title}</h3>
          <ul className="font-mono">
            {lines.map((line) => <li key={line} className="break-all">{line}</li>)}
          </ul>
        </section>
      ))}
    </>
  );
}

/** What the rail shows in developer mode; inert while it is off. */
export interface DeveloperRail {
  enabled: boolean;
  /** The included apps the rail lists under an app (G-20), hidden ones too in developer mode. */
  apps: (item: ChromeMenuNode) => readonly ChromeMenuNode[];
  /** An app's own menus for the top bar (G-20), hidden ones too in developer mode. */
  menus: (item: ChromeMenuNode) => readonly ChromeMenuNode[];
  /** The item's visible label, marked when it is hidden. */
  label: (item: ChromeMenuNode) => string;
  /** The item's id, the layers that shaped it and why it is hidden. */
  describe: (item: ChromeMenuNode) => string | undefined;
  /** Removed items that showed under `parentId` (rail roots when `null`), with their display labels. */
  removedUnder: (parentId: string | null) => readonly (RemovedMenuItem & { displayLabel: string })[];
}

/** Resolve the rail's developer view once per rail; items read it from their rail. */
export function useDeveloperRail(): DeveloperRail {
  const t = useUiT();
  const enabled = useDeveloperMode();
  const composition = useRuntimeComposition();
  const { vocabulary } = useAppRuntime();
  return useMemo(() => {
    const hiddenById = new Map(composition?.menus.hidden.map((entry) => [entry.id, entry]));
    return {
      enabled,
      apps: (item) => (enabled ? item.railChildren(true).filter((child) => child.isApp) : item.appChildren()),
      menus: (item) => (enabled ? item.railChildren(true).filter((child) => !child.isApp) : item.menuItems()),
      label: (item) => (enabled && item.hidden ? t("developer.hiddenItem", { label: item.displayLabel }) : item.displayLabel),
      describe: (item) => {
        if (!enabled) return undefined;
        const layers = layersOf(composition?.menus.provenance[item.id] ?? {});
        const hidden = hiddenById.get(item.id);
        return [
          item.id,
          layers ? t("developer.shapedBy", { layers }) : undefined,
          hidden ? t("developer.hiddenBy", { layer: hidden.by, reason: t(`developer.reason.${hidden.reason}`) }) : undefined,
        ].filter(Boolean).join(" · ");
      },
      removedUnder: (parentId) => {
        if (!enabled || !composition) return [];
        return composition.menus.removed
          .filter((node) => (node.parent ?? null) === parentId)
          .map((node) => ({ ...node, displayLabel: vocabulary?.menus[node.id] ?? node.label ?? node.id }));
      },
    };
  }, [composition, enabled, t, vocabulary]);
}

/** In developer mode, the technical name a field label or column header shows on hover. */
export function useDeveloperFieldTitle(): (field: string, context?: string) => string | undefined {
  const t = useUiT();
  const enabled = useDeveloperMode();
  return useCallback(
    (field, context) => {
      if (!enabled) return undefined;
      return context ? t("developer.fieldIn", { field, context }) : t("developer.field", { field });
    },
    [enabled, t],
  );
}
