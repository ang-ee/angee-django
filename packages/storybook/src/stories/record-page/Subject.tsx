import * as React from "react";
import { Avatar, AvatarStack, Button, Chip, Glyph, StatusbarSteps, Tabs } from "@angee/ui";
import { IconButton } from "./PaneToolbar";
import type { HeaderControls, LifecycleVerb, SubjectData } from "./types";

/**
 * The middle region: the record. One command band (40px): the overflow menu, the lifecycle
 * verbs, then the record-header controls as icons (needs you, following, access) and the pager.
 * Below it the subject card: eyebrow · title · statusbar, the waits line, then the record's own
 * content. A document-backed record shows its source document beside the form, not in a tab.
 */
export function CommandBand({ lifecycle, menu, controls, menuOpen = false }: { lifecycle: readonly LifecycleVerb[]; menu: readonly string[]; controls: HeaderControls; menuOpen?: boolean }): React.ReactElement {
  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border-subtle bg-sheet px-3">
      <div className="relative">
        <IconButton icon="more-vertical" label="Actions" active={menuOpen} />
        {menuOpen ? (
          <ul className="absolute left-0 top-8 z-20 w-56 rounded-8 border border-border-subtle bg-popover p-1 text-13 shadow-popover">
            {menu.map((m) => <li key={m} className="rounded-6 px-2 py-1 hover:bg-canvas">{m}</li>)}
          </ul>
        ) : null}
      </div>
      {lifecycle.map((v) => <Button key={v.label} size="sm" variant={v.tone === "danger" ? "danger" : "primary"}>{v.label}</Button>)}
      <span className="ml-auto flex items-center gap-1">
        {controls.needsMe ? <IconButton icon="triangle-alert" label={`Needs you · ${controls.needsMe}`} badge={controls.needsMe} /> : null}
        <IconButton icon={controls.following ? "bell" : "bell-off"} label={controls.following ? "Following · click to stop" : "Follow"} active={controls.following} />
        <button type="button" title="Shared with · Share" aria-label="Share" className="flex items-center gap-1 rounded-6 px-1 py-0.5 hover:bg-canvas">
          <AvatarStack>{controls.access.map((a) => <Avatar key={a} size="sm" initials={a} />)}</AvatarStack>
          <Glyph name="share" />
        </button>
        {controls.pager ? <>
          <span className="mx-1 h-5 w-px bg-border-subtle" />
          <IconButton icon="chevron-left" label="Previous" /><span className="text-12 tabular-nums text-fg-muted">{controls.pager}</span><IconButton icon="chevron-right" label="Next" />
        </> : null}
      </span>
    </div>
  );
}

export function Subject({ data, menuOpen }: { data: SubjectData; menuOpen?: boolean }): React.ReactElement {
  return (
    <main className="flex min-w-0 flex-1 flex-col bg-canvas">
      <CommandBand lifecycle={data.lifecycle} menu={data.menu} controls={data.controls} menuOpen={menuOpen} />
      <div className="flex min-h-0 flex-1 gap-4 overflow-hidden p-4">
        <section className="flex min-w-0 flex-1 flex-col overflow-y-auto rounded-8 border border-border-subtle bg-sheet">
          <header className="flex items-start justify-between gap-4 px-6 pt-5">
            <div className="min-w-0">
              <p className="text-11 uppercase tracking-wide text-fg-muted">{data.eyebrow}</p>
              <h1 className="truncate text-20 font-semibold leading-7">{data.title}</h1>
              {data.subtitle ? <p className="text-13 text-fg-muted">{data.subtitle}</p> : null}
            </div>
            {data.steps ? <div className="shrink-0"><StatusbarSteps steps={data.steps} value={data.step ?? null} readOnly />{data.waits ? <p className="mt-1 text-right text-12 text-fg-muted">{data.waits}</p> : null}</div> : null}
          </header>
          <div className="px-6 pb-6 pt-5">{data.body}</div>
        </section>
        {data.document ? <DocumentPane document={data.document} /> : null}
      </div>
    </main>
  );
}

/** The evidence beside the form: the file the record was read from, with its own small band. */
export function DocumentPane({ document }: { document: NonNullable<SubjectData["document"]> }): React.ReactElement {
  return (
    <section className="flex w-[38%] shrink-0 flex-col overflow-hidden rounded-8 border border-border-subtle bg-sheet">
      <div className="flex h-8 items-center gap-2 border-b border-border-subtle px-2">
        <Glyph name="file" />
        <span className="min-w-0 truncate text-12 font-medium">{document.title}</span>
        <span className="truncate text-11 text-fg-muted">{document.meta}</span>
        <span className="ml-auto flex items-center"><IconButton icon="arrow-up-right" label="Open file" /><IconButton icon="panel-right" label="Hide document" /></span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas p-3">{document.preview}</div>
    </section>
  );
}

/* Field and section primitives the four pages share */

export const Field = ({ label, value, mark }: { label: string; value: React.ReactNode; mark?: string }) => (
  <div className="min-w-0">
    <p className="text-11 uppercase tracking-wide text-fg-muted">{label}{mark ? <Chip tone="info" variant="soft" className="ml-1 normal-case tracking-normal">{mark}</Chip> : null}</p>
    <div className="mt-0.5 truncate text-13">{value}</div>
  </div>
);
export const FieldGrid = ({ children, cols = 3 }: { children: React.ReactNode; cols?: 2 | 3 }) => <div className={`grid gap-x-6 gap-y-4 ${cols === 2 ? "grid-cols-2" : "grid-cols-3"}`}>{children}</div>;
export const BodyTabs = ({ tabs, active }: { tabs: readonly string[]; active: string }) => (
  <Tabs value={active} variant="page" className="mt-6"><Tabs.List>{tabs.map((tab) => <Tabs.Tab key={tab} value={tab} className="text-12 uppercase tracking-wide">{tab}</Tabs.Tab>)}</Tabs.List></Tabs>
);
export const Placeholder = ({ label, h = "12rem" }: { label: string; h?: string }) => <div className="grid place-items-center rounded-8 border border-dashed border-border-subtle text-12 text-fg-muted" style={{ height: h }}>{label}</div>;
export const LinesTable = ({ rows }: { rows: readonly [string, string, string, string][] }) => (
  <table className="mt-3 w-full text-13">
    <thead><tr className="text-11 uppercase tracking-wide text-fg-muted"><th className="py-1 text-left font-medium">Label</th><th className="py-1 text-right font-medium">Qty</th><th className="py-1 text-right font-medium">Unit</th><th className="py-1 text-right font-medium">Subtotal</th></tr></thead>
    <tbody>{rows.map((r) => <tr key={r[0]} className="border-t border-border-subtle"><td className="py-1.5">{r[0]}</td><td className="py-1.5 text-right tabular-nums">{r[1]}</td><td className="py-1.5 text-right tabular-nums">{r[2]}</td><td className="py-1.5 text-right tabular-nums">{r[3]}</td></tr>)}</tbody>
  </table>
);
