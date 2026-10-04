/**
 * ThemeStudio — interactive design-token editor for Storybook.
 *
 * Architecture:
 * - LEFT: control sidebar — theme picker, color swatches, structural presets.
 * - RIGHT: live component showcase — every change is reflected immediately via
 *   CSS custom-property overrides on the preview container div.
 *
 * The preview container sits INSIDE the existing AppearanceProvider (provided
 * by the Storybook decorator). ThemeStudio applies incremental CSS-var overrides
 * directly as inline `style` on its preview div — this means:
 *   • The active Storybook theme-toolbar selection is the baseline.
 *   • ThemeStudio overrides layer on top without a second provider or conflict.
 *   • The Storybook dark/light global still works for the base theme.
 *
 * This approach is the same mechanism AppearanceProvider uses internally
 * (applyAppearanceRoot sets CSS vars on document.documentElement).
 */

import * as React from "react";

import { AgentCard } from "../fragments/AgentCard";
import { EmptyState } from "../fragments/EmptyState";
import { cn } from "../lib/cn";
import { Alert } from "../ui/alert";
import { Avatar } from "../ui/avatar";
import { Badge, CountBadge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Checkbox } from "../ui/checkbox";
import { Chip } from "../ui/chip";
import { FloatingField } from "../ui/FloatingField";
import { Input, SearchInput } from "../ui/input";
import { Switch } from "../ui/switch";
import {
  BORDER_PRESET_LABELS,
  BORDER_PRESETS,
  DENSITY_LABELS,
  DENSITY_PRESETS,
  ELEVATION_LABELS,
  ELEVATION_PRESETS,
  FONT_LABELS,
  FONT_PRESETS,
  INPUT_LABEL_STYLE_LABELS,
  INPUT_LABEL_STYLES,
  RADIUS_LABELS,
  RADIUS_PRESETS,
  THEME_STUDIO_DEFAULTS,
  buildPreviewOverrides,
  type BorderPreset,
  type InputLabelStyle,
  type ThemeStudioState,
} from "./theme-studio-presets";

// ─── Top-level component ──────────────────────────────────────────────────────

export function ThemeStudio(): React.ReactElement {
  const [state, setState] = React.useState<ThemeStudioState>(THEME_STUDIO_DEFAULTS);
  const [sidebarOpen, setSidebarOpen] = React.useState(true);

  const update = React.useCallback(
    (patch: Partial<ThemeStudioState>) =>
      setState((prev) => ({ ...prev, ...patch })),
    [],
  );

  const previewVars = buildPreviewOverrides(state);

  // Build ThemeCustomization-level overrides as CSS vars for the preview.
  const themeVars = buildThemeVars(state);
  const allVars = { ...themeVars, ...previewVars } as React.CSSProperties;

  return (
    <div
      className="flex h-screen overflow-hidden bg-canvas font-sans text-fg"
      data-color-scheme={state.colorScheme}
    >
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      {sidebarOpen && (
        <aside className="flex w-72 shrink-0 flex-col overflow-y-auto border-r border-border-subtle bg-sheet">
          <SidebarHeader state={state} update={update} onClose={() => setSidebarOpen(false)} />
          <div className="flex-1 space-y-0 divide-y divide-border-subtle">
            <SidebarSection title="Colors">
              <ColorSection state={state} update={update} />
            </SidebarSection>
            <SidebarSection title="Typography">
              <ChipPicker
                label="Font family"
                options={["theme", ...FONT_PRESETS]}
                labels={FONT_LABELS}
                value={state.font}
                onChange={(v) => update({ font: v as ThemeStudioState["font"] })}
              />
            </SidebarSection>
            <SidebarSection title="Shape">
              <ChipPicker
                label="Border radius"
                options={["theme", ...RADIUS_PRESETS]}
                labels={RADIUS_LABELS}
                value={state.radius}
                onChange={(v) => update({ radius: v as ThemeStudioState["radius"] })}
              />
            </SidebarSection>
            <SidebarSection title="Density">
              <ChipPicker
                label="Control size"
                options={["theme", ...DENSITY_PRESETS]}
                labels={DENSITY_LABELS}
                value={state.density}
                onChange={(v) => update({ density: v as ThemeStudioState["density"] })}
              />
            </SidebarSection>
            <SidebarSection title="Elevation">
              <ElevationPicker state={state} update={update} />
            </SidebarSection>
            <SidebarSection title="Borders">
              <ChipPicker
                label="Border weight"
                options={[...BORDER_PRESETS]}
                labels={BORDER_PRESET_LABELS}
                value={state.borders}
                onChange={(v) => update({ borders: v as BorderPreset })}
              />
            </SidebarSection>
            <SidebarSection title="Inputs">
              <ChipPicker
                label="Label style"
                options={[...INPUT_LABEL_STYLES]}
                labels={INPUT_LABEL_STYLE_LABELS}
                value={state.inputLabelStyle}
                onChange={(v) => update({ inputLabelStyle: v as InputLabelStyle })}
              />
            </SidebarSection>
          </div>
          <SidebarFooter state={state} update={update} />
        </aside>
      )}

      {/* ── Preview canvas ───────────────────────────────────────────────── */}
      <div className="relative min-w-0 flex-1 overflow-y-auto">
        {/* Sidebar toggle when closed */}
        {!sidebarOpen && (
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="absolute left-4 top-4 z-10 flex h-8 items-center gap-1.5 rounded-6 border border-border bg-sheet px-3 text-13 font-medium text-fg shadow-xs hover:bg-inset cursor-pointer transition-colors"
          >
            <PanelIcon />
            Theme Studio
          </button>
        )}

        {/* The preview div gets all CSS var overrides injected here */}
        <div
          className="min-h-full bg-canvas p-6"
          data-color-scheme={state.colorScheme}
          style={allVars}
        >
          <ComponentShowcase state={state} />
        </div>
      </div>
    </div>
  );
}

// ─── Sidebar sections ─────────────────────────────────────────────────────────

function SidebarHeader({
  state,
  update,
  onClose,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
  onClose: () => void;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
      <div>
        <p className="text-13 font-semibold text-fg">Theme Studio</p>
        <p className="text-2xs text-fg-muted">@angee/ui design system</p>
      </div>
      <div className="flex items-center gap-1">
        {/* Light/Dark toggle */}
        <button
          type="button"
          aria-label="Toggle color scheme"
          onClick={() =>
            update({ colorScheme: state.colorScheme === "light" ? "dark" : "light" })
          }
          className="grid size-7 place-content-center rounded-6 border border-border text-fg-muted hover:bg-inset hover:text-fg cursor-pointer transition-colors"
          title={`Switch to ${state.colorScheme === "light" ? "dark" : "light"} mode`}
        >
          {state.colorScheme === "light" ? <MoonIcon /> : <SunIcon />}
        </button>
        <button
          type="button"
          aria-label="Close sidebar"
          onClick={onClose}
          className="grid size-7 place-content-center rounded-6 border border-border text-fg-muted hover:bg-inset hover:text-fg cursor-pointer transition-colors"
        >
          <CloseIcon />
        </button>
      </div>
    </div>
  );
}

function SidebarSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="px-4 py-4">
      <p className="mb-3 text-2xs font-semibold uppercase tracking-wide text-fg-muted">
        {title}
      </p>
      {children}
    </div>
  );
}

function SidebarFooter({
  state,
  update,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
}): React.ReactElement {
  return (
    <div className="border-t border-border-subtle px-4 py-3">
      <Button
        variant="ghost"
        size="sm"
        className="w-full"
        onClick={() => update(THEME_STUDIO_DEFAULTS)}
      >
        Reset to defaults
      </Button>
    </div>
  );
}

// ─── Color section ────────────────────────────────────────────────────────────

function ColorSection({
  state,
  update,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
}): React.ReactElement {
  const colorFields: Array<{
    key: keyof Pick<
      ThemeStudioState,
      "brand" | "accent" | "neutral" | "canvas" | "surface" | "rail" | "success" | "warning" | "danger" | "info"
    >;
    label: string;
  }> = [
    { key: "brand",   label: "Brand" },
    { key: "accent",  label: "Accent" },
    { key: "neutral", label: "Neutral" },
    { key: "canvas",  label: "Canvas" },
    { key: "surface", label: "Surface" },
    { key: "rail",    label: "Rail" },
    { key: "success", label: "Success" },
    { key: "warning", label: "Warning" },
    { key: "danger",  label: "Danger" },
    { key: "info",    label: "Info" },
  ];
  return (
    <div className="grid grid-cols-2 gap-2">
      {colorFields.map(({ key, label }) => (
        <ColorSwatch
          key={key}
          label={label}
          value={state[key] as string}
          onChange={(v) => update({ [key]: v })}
        />
      ))}
    </div>
  );
}

function ColorSwatch({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}): React.ReactElement {
  return (
    <label className="flex cursor-pointer items-center gap-2 rounded-6 border border-border bg-canvas px-2 py-1.5 hover:bg-inset transition-colors">
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        className="size-5 shrink-0 cursor-pointer rounded border-0 p-0"
      />
      <span className="min-w-0 truncate text-2xs font-medium text-fg">{label}</span>
    </label>
  );
}

// ─── Chip picker ──────────────────────────────────────────────────────────────

function ChipPicker({
  label,
  options,
  labels,
  value,
  onChange,
}: {
  label: string;
  options: readonly string[];
  labels: Record<string, string>;
  value: string;
  onChange: (v: string) => void;
}): React.ReactElement {
  return (
    <div className="space-y-2">
      <p className="text-2xs text-fg-muted">{label}</p>
      <div className="flex flex-wrap gap-1">
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt)}
            className={cn(
              "h-6 rounded-full border px-2.5 text-2xs font-medium cursor-pointer transition-colors",
              value === opt
                ? "border-brand bg-brand-soft text-brand-soft-text"
                : "border-border bg-sheet text-fg-muted hover:border-border-strong hover:text-fg",
            )}
          >
            {labels[opt] ?? opt}
          </button>
        ))}
      </div>
    </div>
  );
}

// ─── Elevation picker ─────────────────────────────────────────────────────────

function ElevationPicker({
  state,
  update,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
}): React.ReactElement {
  return (
    <div className="space-y-2">
      <p className="text-2xs text-fg-muted">Shadow depth</p>
      <div className="grid grid-cols-2 gap-1.5">
        {(["theme", ...ELEVATION_PRESETS] as const).map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => update({ elevation: opt as ThemeStudioState["elevation"] })}
            className={cn(
              "h-10 rounded-6 border px-3 text-2xs font-medium cursor-pointer transition-all",
              state.elevation === opt
                ? "border-brand bg-brand-soft text-brand-soft-text"
                : "border-border bg-sheet text-fg-muted hover:text-fg",
              // Show a sample shadow on the button itself
              opt === "flat"     && "shadow-none",
              opt === "subtle"   && "shadow-xs",
              opt === "soft"     && "shadow-sm",
              opt === "dramatic" && "shadow-md",
            )}
          >
            {ELEVATION_LABELS[opt]}
          </button>
        ))}
      </div>
    </div>
  );
}

// ─── Component showcase ───────────────────────────────────────────────────────

function ComponentShowcase({ state }: { state: ThemeStudioState }): React.ReactElement {
  const useFloating = state.inputLabelStyle === "floating";

  return (
    <div className="mx-auto max-w-5xl space-y-10">
      {/* Header */}
      <div className="space-y-1">
        <h1 className="text-28 font-bold text-fg">Component Preview</h1>
        <p className="text-fg-muted">
          Live preview of every component with your current settings.
        </p>
      </div>

      {/* Typography */}
      <ShowcaseSection title="Typography">
        <div className="space-y-3">
          <p className="text-34 font-bold text-fg leading-tight">Display — 34px Bold</p>
          <p className="text-28 font-bold text-fg leading-tight">Heading 1 — 28px Bold</p>
          <p className="text-22 font-semibold text-fg">Heading 2 — 22px Semibold</p>
          <p className="text-lg font-semibold text-fg">Heading 3 — 18px Semibold</p>
          <p className="text-base font-medium text-fg">Heading 4 — 16px Medium</p>
          <p className="text-sm text-fg">Body — 14px Regular. The quick brown fox jumps over the lazy dog.</p>
          <p className="text-13 text-fg-muted">Small — 13px. Secondary content and descriptions live here.</p>
          <p className="text-xs text-fg-muted">Extra small — 12px. Labels, hints, timestamps.</p>
          <p className="text-2xs text-fg-subtle">Nano — 11px. Captions, eyebrows, micro labels.</p>
          <p className="text-sm text-link hover:text-brand cursor-pointer">Link text — hover to see brand color</p>
          <p className="font-mono text-sm text-fg">Monospace — code and technical content</p>
        </div>
      </ShowcaseSection>

      {/* Buttons */}
      <ShowcaseSection title="Buttons">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary">Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Danger</Button>
            <Button variant="link">Link</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm">Small</Button>
            <Button variant="primary" size="md">Medium</Button>
            <Button variant="primary" size="lg">Large</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" loading loadingText="Saving…">Save</Button>
            <Button variant="secondary" disabled>Disabled</Button>
          </div>
        </div>
      </ShowcaseSection>

      {/* Badges + Chips */}
      <ShowcaseSection title="Badges & Chips">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">Neutral</Badge>
            <Badge tone="brand">Brand</Badge>
            <Badge tone="success">Success</Badge>
            <Badge tone="warning">Warning</Badge>
            <Badge tone="danger">Danger</Badge>
            <Badge tone="info">Info</Badge>
            <Badge tone="accent">Accent</Badge>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="brand" variant="solid">Solid</Badge>
            <Badge tone="brand" variant="soft">Soft</Badge>
            <Badge tone="brand" variant="surface">Surface</Badge>
            <Badge tone="brand" variant="outline">Outline</Badge>
            <Badge tone="brand" variant="ghost">Ghost</Badge>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <CountBadge value={3} />
            <CountBadge tone="brand" value={12} />
            <CountBadge tone="danger" value={128} max={99} />
            <Chip tone="neutral">Label</Chip>
            <Chip tone="brand">Active</Chip>
            <Chip tone="success">Done</Chip>
          </div>
        </div>
      </ShowcaseSection>

      {/* Inputs */}
      <ShowcaseSection title="Inputs">
        <div className="grid gap-4 sm:grid-cols-2">
          {useFloating ? (
            <>
              <FloatingField label="Full name" type="text" />
              <FloatingField label="Email address" type="email" />
              <FloatingField label="Required field" type="text" required description="Enter a value to continue." />
              <FloatingField label="Invalid value" type="email" defaultValue="bad-email" invalid error="Invalid email format." />
            </>
          ) : (
            <>
              <div className="grid gap-1.5">
                <label className="text-13 font-medium text-fg">Full name</label>
                <Input type="text" placeholder="Ada Lovelace" />
              </div>
              <div className="grid gap-1.5">
                <label className="text-13 font-medium text-fg">Email address</label>
                <Input type="email" placeholder="ada@example.com" />
              </div>
              <div className="col-span-full">
                <SearchInput placeholder="Search components…" />
              </div>
            </>
          )}
          {!useFloating && (
            <div className="col-span-full">
              <SearchInput placeholder="Search components…" />
            </div>
          )}
          {useFloating && (
            <div className="col-span-full">
              <SearchInput placeholder="Search components…" />
            </div>
          )}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-4">
          <label className="flex cursor-pointer items-center gap-2 text-13 text-fg">
            <Checkbox defaultChecked />
            Remember me
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-13 text-fg">
            <Checkbox />
            Subscribe to updates
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-13 text-fg">
            <Switch defaultChecked />
            Notifications
          </label>
        </div>
      </ShowcaseSection>

      {/* Alerts */}
      <ShowcaseSection title="Alerts">
        <div className="grid gap-3">
          <Alert tone="info" title="Information">
            Your subscription will renew in 7 days.
          </Alert>
          <Alert tone="success" title="Synced">
            All records are up to date.
          </Alert>
          <Alert tone="warning" title="Review needed">
            Three fields require confirmation before saving.
          </Alert>
          <Alert tone="danger" title="Failed">
            The import could not be completed. Check your file format.
          </Alert>
        </div>
      </ShowcaseSection>

      {/* Cards */}
      <ShowcaseSection title="Cards">
        <div className="grid gap-4 sm:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>Default card</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-13 text-fg-muted">Standard sheet surface with subtle border.</p>
            </CardContent>
          </Card>
          <Card variant="elevated">
            <CardHeader>
              <CardTitle>Elevated card</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-13 text-fg-muted">Raised above canvas with a visible shadow.</p>
            </CardContent>
          </Card>
          <Card interactive onClick={() => undefined}>
            <CardHeader>
              <CardTitle>Interactive card</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-13 text-fg-muted">Hover to see the interactive state treatment.</p>
            </CardContent>
          </Card>
        </div>
      </ShowcaseSection>

      {/* Avatars */}
      <ShowcaseSection title="Avatars">
        <div className="flex flex-wrap items-end gap-3">
          <Avatar initials="AL" size="sm" />
          <Avatar initials="AL" size="md" />
          <Avatar initials="BM" size="lg" />
          <Avatar initials="CD" size="xl" />
          <Avatar initials="EF" size="xxl" />
        </div>
      </ShowcaseSection>

      {/* Agent cards */}
      <ShowcaseSection title="Agent Cards">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <AgentCard
            name="Research Agent"
            model="claude-sonnet-4"
            icon="cpu"
            status="running"
            statusMessage="Scanning 47 documents…"
            progress={62}
            startedAt={new Date(Date.now() - 93_000)}
            actions={[
              { label: "Pause", icon: "pause", onClick: () => undefined },
              { label: "Stop",  icon: "square", variant: "danger", onClick: () => undefined },
            ]}
          />
          <AgentCard
            name="Build Agent"
            model="codex"
            icon="hammer"
            status="success"
            statusMessage="All files compiled."
            progress={100}
            startedAt={new Date(Date.now() - 240_000)}
            actions={[{ label: "View output", icon: "external-link", onClick: () => undefined }]}
          />
          <AgentCard
            name="Ingest Pipeline"
            model="gpt-4o"
            icon="database"
            status="error"
            statusMessage="Connection timed out."
            startedAt={new Date(Date.now() - 18_000)}
            actions={[
              { label: "Retry", icon: "rotate-ccw", onClick: () => undefined },
            ]}
          />
        </div>
      </ShowcaseSection>

      {/* Empty state */}
      <ShowcaseSection title="Empty State">
        <EmptyState
          icon="archive"
          title="No records found"
          description="Create the first record or adjust the active filters to see results here."
          actions={
            <>
              <Button size="sm" variant="secondary">Clear filters</Button>
              <Button size="sm" variant="primary">New record</Button>
            </>
          }
        />
      </ShowcaseSection>
    </div>
  );
}

function ShowcaseSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-15 font-semibold text-fg">{title}</h2>
        <span className="h-px flex-1 bg-border-subtle" />
      </div>
      {children}
    </section>
  );
}

// ─── CSS var builder ──────────────────────────────────────────────────────────

/** Map ThemeStudioState fields to CSS custom properties.
 *  Only emits vars for fields that differ from "theme" (the no-op value). */
function buildThemeVars(state: ThemeStudioState): Record<string, string> {
  const vars: Record<string, string> = {};

  // Colors — minimal, just set the primary brand / accent which are single-var
  // overrides. Full palette computation (light/dark ramps) lives in runtime.mjs;
  // here we just do direct overrides so the preview is immediately responsive.
  if (state.brand   !== THEME_STUDIO_DEFAULTS.brand)   vars["--brand"] = state.brand;
  if (state.accent  !== THEME_STUDIO_DEFAULTS.accent)  vars["--accent"] = state.accent;
  if (state.canvas  !== THEME_STUDIO_DEFAULTS.canvas)  vars["--surface-canvas"] = state.canvas;
  if (state.surface !== THEME_STUDIO_DEFAULTS.surface) vars["--surface-sheet"] = state.surface;

  // Radius — map to CSS vars following RADIUS_TOKENS from runtime.mjs.
  const RADIUS_MAP: Record<string, string[]> = {
    square:   ["0px",  "0px",  "0px",  "0px",  "0px",  "0px"],
    compact:  ["1px",  "2px",  "4px",  "6px",  "8px",  "10px"],
    standard: ["2px",  "4px",  "6px",  "8px",  "10px", "12px"],
    soft:     ["4px",  "6px",  "8px",  "10px", "12px", "14px"],
    round:    ["6px",  "8px",  "12px", "16px", "20px", "24px"],
  };
  if (state.radius !== "theme") {
    const vals = RADIUS_MAP[state.radius];
    if (vals && vals.length === 6) {
      vars["--r-2"]  = vals[0]!;
      vars["--r-4"]  = vals[1]!;
      vars["--r-6"]  = vals[2]!;
      vars["--r-8"]  = vals[3]!;
      vars["--r-10"] = vals[4]!;
      vars["--r-12"] = vals[5]!;
    }
  }

  // Density — map to CSS vars following DENSITY_TOKENS from runtime.mjs.
  const DENSITY_MAP: Record<string, string[]> = {
    compact:     ["24px", "28px", "34px"],
    balanced:    ["26px", "32px", "38px"],
    comfortable: ["28px", "34px", "40px"],
    spacious:    ["30px", "38px", "44px"],
  };
  if (state.density !== "theme") {
    const vals = DENSITY_MAP[state.density];
    if (vals && vals.length === 3) {
      vars["--control-h-sm"] = vals[0]!;
      vars["--control-h-md"] = vals[1]!;
      vars["--control-h-lg"] = vals[2]!;
    }
  }

  // Font
  const FONT_MAP: Record<string, string> = {
    system:     'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    inter:      'Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    humanist:   '"Avenir Next", "Segoe UI", system-ui, sans-serif',
    industrial: '"IBM Plex Sans", Arial, sans-serif',
    editorial:  'Georgia, "Times New Roman", serif',
    mono:       '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
  };
  const fontVal = FONT_MAP[state.font];
  if (state.font !== "theme" && fontVal) {
    vars["--font-family-sans"] = fontVal;
  }

  return vars;
}

// ─── Minimal inline SVG icons (no dependency on icon registry) ───────────────

function MoonIcon(): React.ReactElement {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}
function SunIcon(): React.ReactElement {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}
function CloseIcon(): React.ReactElement {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}
function PanelIcon(): React.ReactElement {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
      <line x1="9" y1="3" x2="9" y2="21" />
    </svg>
  );
}
