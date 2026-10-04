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
import { InlineField } from "../ui/FloatingField";
import { Input, SearchInput } from "../ui/input";
import { Command } from "../ui/command";
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
  MOTION_PRESET_LABELS,
  MOTION_PRESETS,
  RADIUS_LABELS,
  RADIUS_PRESETS,
  THEME_STUDIO_DEFAULTS,
  buildPreviewOverrides,
  type BorderPreset,
  type InputLabelStyle,
  type MotionPreset,
  type ThemeStudioState,
} from "./theme-studio-presets";
import {
  GOOGLE_FONTS,
  GOOGLE_FONTS_COUNT,
  CATEGORY_LABELS,
} from "./google-fonts-list";

// ─── Google Font loader ───────────────────────────────────────────────────────

/** Injects a Google Fonts <link> into document.head when fontName changes.
 *  Idempotent — re-runs only when fontName changes, never duplicates tags. */
function useGoogleFont(fontName: string | null): void {
  React.useEffect(() => {
    if (!fontName) return;
    const id = `gf-${fontName.replace(/\s+/g, "-").toLowerCase()}`;
    if (document.getElementById(id)) return;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = `https://fonts.googleapis.com/css2?family=${encodeURIComponent(fontName)}:wght@400;500;600;700&display=swap`;
    document.head.appendChild(link);
  }, [fontName]);
}

// ─── localStorage persistence ─────────────────────────────────────────────────

const STORAGE_KEY = "angee:theme-studio";

function loadState(): ThemeStudioState {
  try {
    const raw = typeof window !== "undefined"
      ? window.localStorage.getItem(STORAGE_KEY)
      : null;
    if (!raw) return THEME_STUDIO_DEFAULTS;
    const parsed = JSON.parse(raw);
    // Merge with defaults to handle new fields added after the user saved.
    return { ...THEME_STUDIO_DEFAULTS, ...parsed };
  } catch {
    return THEME_STUDIO_DEFAULTS;
  }
}

function saveState(state: ThemeStudioState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // localStorage unavailable (private browsing quota, etc.) — silent.
  }
}

// ─── Top-level component ──────────────────────────────────────────────────────

/**
 * Called by the Apply button with the merged CSS-var overrides computed from
 * the current ThemeStudio state. The host (Storybook story) is responsible for
 * distributing those overrides to all other stories via whatever mechanism it
 * owns — e.g. Storybook globals. ThemeStudio itself has no dependency on
 * Storybook APIs.
 */
export interface ThemeStudioProps {
  onApply?: (cssVars: Record<string, string>) => void;
}

export function ThemeStudio({ onApply }: ThemeStudioProps = {}): React.ReactElement {
  const [state, setState] = React.useState<ThemeStudioState>(loadState);
  const [sidebarOpen, setSidebarOpen] = React.useState(true);

  const update = React.useCallback(
    (patch: Partial<ThemeStudioState>) =>
      setState((prev) => {
        const next = { ...prev, ...patch };
        saveState(next);
        return next;
      }),
    [],
  );

  // Load Google Font into document.head whenever it changes
  useGoogleFont(state.googleFont);

  const previewVars = buildPreviewOverrides(state);

  // Build ThemeCustomization-level overrides as CSS vars for the preview.
  const themeVars = buildThemeVars(state);

  // Font: must be applied as a direct inline `fontFamily` because Tailwind 4
  // compiles `font-sans` as `font-family: var(--font-sans)` where `--font-sans`
  // is a @theme var on :root that doesn't inherit through child div inline styles.
  const fontFamilyOverride: React.CSSProperties = {};
  if (themeVars["--font-sans"]) {
    fontFamilyOverride.fontFamily = themeVars["--font-sans"] as string;
  }

  // Density zoom: scales ALL spacing (gap, padding, margin, font-size, shadow…)
  // uniformly via the CSS `zoom` property. This is the only reliable way to
  // scale Tailwind's hardcoded px utilities without recompiling the stylesheet.
  // --control-h-* tokens are still emitted for theme-export fidelity, but zoom
  // is what the user actually sees in the preview.
  // Read zoom from the --density-zoom var emitted by buildThemeVars so it stays
  // DRY — one source of truth for the zoom value.
  const zoomOverride: React.CSSProperties = {};
  const zoomFromVars = themeVars["--density-zoom"];
  if (zoomFromVars) {
    const z = parseFloat(zoomFromVars);
    if (!isNaN(z) && z !== 1) zoomOverride.zoom = z;
  }

  const allVars = {
    ...themeVars,
    ...previewVars,
    ...fontFamilyOverride,
    ...zoomOverride,
  } as React.CSSProperties;

  // Storybook sidebar colors — fixed light palette, immune to whatever
  // colorScheme the preview canvas is set to. All values are absolute hex/rgba
  // so no CSS-var cascade from the preview can bleed in.
  const SB_BG      = "#f0f1f3"; // light warm grey — almost white, like Figma/Storybook Controls
  const SB_TEXT    = "#1a1d23"; // near-black text for contrast on light bg
  const SB_MUTED   = "#6b7280"; // muted grey for labels/secondary text
  const SB_BORDER  = "rgba(0,0,0,0.08)";
  const SB_HOVER   = "rgba(0,0,0,0.06)";
  // Active chip: strong enough fill so the dark text reads cleanly on light bg.
  const SB_ACTIVE  = "#1a1d23"; // near-black fill — text will be #f0f1f3 (inverse)
  const SB_ACTIVE_TEXT = "#f0f1f3"; // inverse of SB_TEXT for active chip label

  return (
    // No margin/padding on the outer wrapper — fills the Storybook iframe edge-to-edge.
    <div
      className="flex overflow-hidden font-sans"
      style={{ height: "100vh", width: "100vw", margin: 0, padding: 0 }}
    >
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      {sidebarOpen && (
        <aside
          className="flex w-72 shrink-0 flex-col overflow-y-auto"
          style={{
            background: SB_BG,
            color: SB_TEXT,
            borderRight: `1px solid ${SB_BORDER}`,
            // Explicitly light colorScheme so native widgets (scrollbar, color
            // picker) render in light mode. This must match the light SB_BG
            // palette and must NOT be "dark" — that was causing a mismatch
            // between the native-widget rendering and the actual light colours.
            // The sidebar has no data-color-scheme attribute so it is NOT in
            // the Angee appearance cascade; all its colours are absolute inline
            // styles and therefore immune to preview CSS-var changes.
            colorScheme: "light",
          }}
        >
          <SidebarHeader
            onClose={() => setSidebarOpen(false)}
            sbText={SB_TEXT}
            sbMuted={SB_MUTED}
            sbBorder={SB_BORDER}
            sbHover={SB_HOVER}
          />
          <div className="flex-1 overflow-y-auto">
            <SidebarSection title="Colors" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <ColorSection state={state} update={update} sbBorder={SB_BORDER} sbHover={SB_HOVER} sbText={SB_TEXT} sbMuted={SB_MUTED} />
            </SidebarSection>
            <SidebarSection title="Typography" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <FontPicker state={state} update={update} sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER} sbHover={SB_HOVER} sbActive={SB_ACTIVE} sbActiveText={SB_ACTIVE_TEXT} />
            </SidebarSection>
            <SidebarSection title="Shape" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <SbChipPicker
                options={["theme", ...RADIUS_PRESETS]}
                labels={RADIUS_LABELS}
                value={state.radius}
                onChange={(v) => update({ radius: v as ThemeStudioState["radius"] })}
                sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER} sbHover={SB_HOVER} sbActive={SB_ACTIVE} sbActiveText={SB_ACTIVE_TEXT}
              />
            </SidebarSection>
            <SidebarSection title="Density" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <SbChipPicker
                options={["theme", ...DENSITY_PRESETS]}
                labels={DENSITY_LABELS}
                value={state.density}
                onChange={(v) => update({ density: v as ThemeStudioState["density"] })}
                sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER} sbHover={SB_HOVER} sbActive={SB_ACTIVE} sbActiveText={SB_ACTIVE_TEXT}
              />
            </SidebarSection>
            <SidebarSection title="Elevation" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <SbChipPicker
                options={["theme", ...ELEVATION_PRESETS]}
                labels={ELEVATION_LABELS}
                value={state.elevation}
                onChange={(v) => update({ elevation: v as ThemeStudioState["elevation"] })}
                sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER} sbHover={SB_HOVER} sbActive={SB_ACTIVE} sbActiveText={SB_ACTIVE_TEXT}
              />
            </SidebarSection>
            <SidebarSection title="Borders" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <SbChipPicker
                options={[...BORDER_PRESETS]}
                labels={BORDER_PRESET_LABELS}
                value={state.borders}
                onChange={(v) => update({ borders: v as BorderPreset })}
                sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER} sbHover={SB_HOVER} sbActive={SB_ACTIVE} sbActiveText={SB_ACTIVE_TEXT}
              />
            </SidebarSection>
            <SidebarSection title="Inputs" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <SbChipPicker
                options={[...INPUT_LABEL_STYLES]}
                labels={INPUT_LABEL_STYLE_LABELS}
                value={state.inputLabelStyle}
                onChange={(v) => update({ inputLabelStyle: v as InputLabelStyle })}
                sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER} sbHover={SB_HOVER} sbActive={SB_ACTIVE} sbActiveText={SB_ACTIVE_TEXT}
              />
            </SidebarSection>
            <SidebarSection title="Motion" sbText={SB_TEXT} sbMuted={SB_MUTED} sbBorder={SB_BORDER}>
              <MotionSection
                state={state}
                update={update}
                sbText={SB_TEXT}
                sbMuted={SB_MUTED}
                sbBorder={SB_BORDER}
                sbHover={SB_HOVER}
                sbActive={SB_ACTIVE}
                sbActiveText={SB_ACTIVE_TEXT}
              />
            </SidebarSection>
          </div>
          <SidebarFooter
            state={state}
            update={update}
            themeVars={themeVars}
            previewVars={previewVars}
            onApply={onApply}
            sbText={SB_TEXT}
            sbBorder={SB_BORDER}
            sbHover={SB_HOVER}
            sbActive={SB_ACTIVE}
            sbActiveText={SB_ACTIVE_TEXT}
          />
        </aside>
      )}

      {/* ── Preview canvas ───────────────────────────────────────────────── */}
      <div className="relative min-w-0 flex-1 overflow-y-auto bg-canvas">
        {/* Sidebar toggle when closed */}
        {!sidebarOpen && (
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            style={{ background: SB_BG, color: SB_TEXT, borderColor: SB_BORDER }}
            className="absolute left-0 top-0 z-10 flex h-8 items-center gap-1.5 border-b border-r px-3 text-13 font-medium cursor-pointer transition-colors hover:opacity-80"
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

// ─── Sidebar theme tokens (Storybook-native dark palette) ─────────────────────
// These are passed as props so every sidebar sub-component uses inline styles
// and is immune to whatever CSS vars the user has applied to the preview.
interface SbTheme {
  sbBg: string;
  sbText: string;
  sbMuted: string;
  sbBorder: string;
  sbHover: string;
}

// ─── Sidebar sections ─────────────────────────────────────────────────────────

function SidebarHeader({
  onClose,
  sbText,
  sbMuted,
  sbBorder,
  sbHover,
}: {
  onClose: () => void;
} & Pick<SbTheme, "sbText" | "sbMuted" | "sbBorder" | "sbHover">): React.ReactElement {
  return (
    <div
      className="flex shrink-0 items-center justify-between px-4 py-3"
      style={{ borderBottom: `1px solid ${sbBorder}` }}
    >
      <div>
        <p className="text-13 font-semibold" style={{ color: sbText }}>
          Theme Studio
        </p>
        <p className="text-2xs" style={{ color: sbMuted }}>
          @angee/ui component library
        </p>
      </div>
      <SbIconButton
        aria-label="Close sidebar"
        onClick={onClose}
        sbBorder={sbBorder}
        sbHover={sbHover}
        sbText={sbMuted}
      >
        <CloseIcon />
      </SbIconButton>
    </div>
  );
}

function SidebarSection({
  title,
  children,
  sbText,
  sbMuted,
  sbBorder,
}: {
  title: string;
  children: React.ReactNode;
} & Pick<SbTheme, "sbText" | "sbMuted" | "sbBorder">): React.ReactElement {
  return (
    <div
      className="px-4 py-3"
      style={{ borderBottom: `1px solid ${sbBorder}` }}
    >
      <p
        className="mb-2.5 text-2xs font-semibold uppercase tracking-widest"
        style={{ color: sbMuted, letterSpacing: "0.08em" }}
      >
        {title}
      </p>
      <div style={{ color: sbText }}>
        {children}
      </div>
    </div>
  );
}

function SidebarFooter({
  state,
  update,
  themeVars,
  previewVars,
  onApply,
  sbText,
  sbBorder,
  sbHover,
  sbActive,
  sbActiveText,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
  /** CSS vars that buildThemeVars() produced for the current state. */
  themeVars: Record<string, string>;
  /** CSS vars that buildPreviewOverrides() produced for the current state. */
  previewVars: Record<string, string>;
  onApply?: (cssVars: Record<string, string>) => void;
} & Pick<SbTheme, "sbText" | "sbBorder" | "sbHover"> & { sbActive: string; sbActiveText: string }): React.ReactElement {
  const [applied, setApplied] = React.useState(false);

  function handleApply() {
    const overrides: Record<string, string> = { ...themeVars, ...previewVars };
    onApply?.(overrides);
    // Flash "Applied ✓" feedback for 1.5 s then restore.
    setApplied(true);
    setTimeout(() => setApplied(false), 1500);
  }

  return (
    <div
      className="shrink-0 space-y-1 px-4 py-3"
      style={{ borderTop: `1px solid ${sbBorder}` }}
    >
      {/* Primary action: apply current settings to all stories */}
      <button
        type="button"
        className="w-full rounded py-1.5 text-2xs font-semibold cursor-pointer transition-colors text-center"
        style={{
          background: applied ? sbActive : sbActive,
          color: applied ? sbActiveText : sbActiveText,
          opacity: applied ? 0.75 : 1,
        }}
        onClick={handleApply}
      >
        {applied ? "Applied ✓" : "Apply to all stories"}
      </button>

      {/* Secondary action: reset */}
      <button
        type="button"
        className="w-full rounded py-1.5 text-2xs font-medium cursor-pointer transition-colors text-center"
        style={{ color: sbText, background: "transparent" }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = sbHover; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
        onClick={() => update(THEME_STUDIO_DEFAULTS)}
      >
        Reset to defaults
      </button>
    </div>
  );
}

/** Small icon button styled for the dark Storybook sidebar. */
function SbIconButton({
  children,
  sbBorder,
  sbHover,
  sbText,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & Pick<SbTheme, "sbBorder" | "sbHover" | "sbText">): React.ReactElement {
  return (
    <button
      type="button"
      className="grid size-7 cursor-pointer place-content-center rounded transition-colors"
      style={{ color: sbText, border: `1px solid ${sbBorder}`, background: "transparent" }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = sbHover; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
      {...props}
    >
      {children}
    </button>
  );
}

// ─── Color section ────────────────────────────────────────────────────────────

function ColorSection({
  state,
  update,
  sbBorder,
  sbHover,
  sbText,
  sbMuted,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
} & Pick<SbTheme, "sbBorder" | "sbHover" | "sbText" | "sbMuted">): React.ReactElement {
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
    <div className="grid grid-cols-2 gap-1.5">
      {colorFields.map(({ key, label }) => (
        <ColorSwatch
          key={key}
          label={label}
          value={state[key] as string}
          onChange={(v) => update({ [key]: v })}
          sbBorder={sbBorder}
          sbHover={sbHover}
          sbText={sbText}
          sbMuted={sbMuted}
        />
      ))}
    </div>
  );
}

function ColorSwatch({
  label,
  value,
  onChange,
  sbBorder,
  sbHover,
  sbText,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
} & Pick<SbTheme, "sbBorder" | "sbHover" | "sbText" | "sbMuted">): React.ReactElement {
  return (
    <label
      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 transition-colors"
      style={{ border: `1px solid ${sbBorder}`, background: "transparent" }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLLabelElement).style.background = sbHover; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLLabelElement).style.background = "transparent"; }}
    >
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        className="size-5 shrink-0 cursor-pointer rounded border-0 p-0"
      />
      <span className="min-w-0 truncate text-2xs font-medium" style={{ color: sbText }}>{label}</span>
    </label>
  );
}

// ─── SbChipPicker — chip picker styled for dark Storybook sidebar ─────────────

function SbChipPicker({
  options,
  labels,
  value,
  onChange,
  sbText,
  sbMuted,
  sbBorder,
  sbHover,
  sbActive,
  sbActiveText,
}: {
  options: readonly string[];
  labels: Record<string, string>;
  value: string;
  onChange: (v: string) => void;
} & Pick<SbTheme, "sbText" | "sbMuted" | "sbBorder" | "sbHover"> & { sbActive: string; sbActiveText: string }): React.ReactElement {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map((opt) => {
        const isActive = value === opt;
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt)}
            className="h-6 rounded-full px-2.5 text-2xs font-medium cursor-pointer transition-colors"
            style={{
              // Active: dark fill + inverse text — high contrast on light sidebar.
              // Inactive: transparent background with muted border.
              border: `1px solid ${isActive ? "transparent" : sbBorder}`,
              background: isActive ? sbActive : "transparent",
              color: isActive ? sbActiveText : sbText,
            }}
            onMouseEnter={(e) => {
              if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = sbHover;
            }}
            onMouseLeave={(e) => {
              if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            }}
          >
            {labels[opt] ?? opt}
          </button>
        );
      })}
    </div>
  );
}

// ─── Motion section ───────────────────────────────────────────────────────────

function MotionSection({
  state,
  update,
  sbText,
  sbMuted,
  sbBorder,
  sbHover,
  sbActive,
  sbActiveText,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
} & Pick<SbTheme, "sbText" | "sbMuted" | "sbBorder" | "sbHover"> & { sbActive: string; sbActiveText: string }): React.ReactElement {
  // Speed multiplier: 0.25 → 3× in 0.25 steps
  const SPEED_MIN = 0;
  const SPEED_MAX = 3;

  // Display label for current speed
  const speedLabel = state.motionPreset === "none"
    ? "—"
    : `${state.motionSpeed.toFixed(2).replace(/\.?0+$/, "")}×`;

  return (
    <div className="space-y-3">
      {/* Preset chips */}
      <SbChipPicker
        options={[...MOTION_PRESETS]}
        labels={MOTION_PRESET_LABELS}
        value={state.motionPreset}
        onChange={(v) => {
          const preset = v as MotionPreset;
          // When switching to none, reset speed to 0; otherwise restore 1
          update({
            motionPreset: preset,
            motionSpeed: preset === "none" ? 0 : 1,
          });
        }}
        sbText={sbText} sbMuted={sbMuted} sbBorder={sbBorder}
        sbHover={sbHover} sbActive={sbActive} sbActiveText={sbActiveText}
      />

      {/* Speed slider — hidden when preset is "none" */}
      {state.motionPreset !== "none" && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <p className="text-2xs" style={{ color: sbMuted }}>Speed</p>
            <span className="text-2xs font-medium tabular-nums" style={{ color: sbText }}>
              {speedLabel}
            </span>
          </div>
          <input
            type="range"
            min={SPEED_MIN}
            max={SPEED_MAX}
            step={0.05}
            value={state.motionSpeed}
            onChange={(e) => update({ motionSpeed: parseFloat(e.currentTarget.value) })}
            className="w-full cursor-pointer appearance-none rounded-full"
            style={{
              height: "4px",
              accentColor: sbActive,
              background: `linear-gradient(to right, ${sbActive} ${((state.motionSpeed - SPEED_MIN) / (SPEED_MAX - SPEED_MIN)) * 100}%, ${sbBorder} 0%)`,
            }}
          />
          <div className="flex justify-between">
            <span className="text-2xs" style={{ color: sbMuted }}>Instant</span>
            <span className="text-2xs" style={{ color: sbMuted }}>3×</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Font picker ──────────────────────────────────────────────────────────────
// Uses the Command component (cmdk-based) which has built-in search +
// virtualized list — perfect for 400+ fonts.

/**
 * Injects a minimal Google Fonts stylesheet for a batch of font families.
 * Uses the `text=` parameter so only the glyphs needed to render each
 * font's own name are downloaded — typically < 2 KB per font.
 */
function usePreviewFonts(families: readonly string[]): void {
  React.useEffect(() => {
    if (!families.length) return;
    // Batch up to 50 fonts per request to stay within URL length limits.
    const BATCH = 50;
    for (let i = 0; i < families.length; i += BATCH) {
      const batch = families.slice(i, i + BATCH);
      const id = `gf-preview-${i}`;
      if (document.getElementById(id)) continue;

      // Build a combined text parameter containing all unique characters
      // needed to render every family name in this batch.
      const chars = [...new Set(batch.join("").split(""))].join("");
      const familyParams = batch
        .map((f) => `family=${encodeURIComponent(f)}`)
        .join("&");
      const href = `https://fonts.googleapis.com/css2?${familyParams}&text=${encodeURIComponent(chars)}&display=swap`;

      const link = document.createElement("link");
      link.id = id;
      link.rel = "stylesheet";
      link.href = href;
      document.head.appendChild(link);
    }
  // Only run once — families list is derived from a static constant.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

function FontPicker({
  state,
  update,
  sbText,
  sbMuted,
  sbBorder,
  sbHover,
  sbActive,
  sbActiveText,
}: {
  state: ThemeStudioState;
  update: (patch: Partial<ThemeStudioState>) => void;
} & Pick<SbTheme, "sbText" | "sbMuted" | "sbBorder" | "sbHover"> & { sbActive: string; sbActiveText: string }): React.ReactElement {
  const isGoogleActive = state.googleFont !== null;

  // Load preview stylesheets for all font names (minimal text= subset).
  const allFamilies = React.useMemo(
    () => GOOGLE_FONTS.map((f) => f.family),
    [],
  );
  usePreviewFonts(allFamilies);

  // Group fonts by category for Command.Group
  const byCategory = React.useMemo(() => {
    const map = new Map<string, typeof GOOGLE_FONTS[number][]>();
    for (const font of GOOGLE_FONTS) {
      const cat = CATEGORY_LABELS[font.category];
      const list = map.get(cat) ?? [];
      list.push(font);
      map.set(cat, list);
    }
    return map;
  }, []);

  const categoryOrder = ["Sans Serif", "Serif", "Display", "Monospace", "Handwriting"];

  return (
    <div className="space-y-3">
      {/* System presets — quick chips */}
      <div className="space-y-1.5">
        <p className="text-2xs" style={{ color: sbMuted }}>Built-in presets</p>
        <div className="flex flex-wrap gap-1">
          {(["theme", ...FONT_PRESETS] as const).map((opt) => {
            const isActive = !isGoogleActive && state.font === opt;
            return (
              <button
                key={opt}
                type="button"
                onClick={() => update({ font: opt as ThemeStudioState["font"], googleFont: null })}
                className="h-6 rounded-full px-2.5 text-2xs font-medium cursor-pointer transition-colors"
                style={{
                  // Same active treatment as SbChipPicker: dark fill + inverse text.
                  border: `1px solid ${isActive ? "transparent" : sbBorder}`,
                  background: isActive ? sbActive : "transparent",
                  color: isActive ? sbActiveText : sbText,
                }}
                onMouseEnter={(e) => { if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = sbHover; }}
                onMouseLeave={(e) => { if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
              >
                {FONT_LABELS[opt] ?? opt}
              </button>
            );
          })}
        </div>
      </div>

      {/* Google Fonts — Command with built-in search */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <p className="text-2xs" style={{ color: sbMuted }}>
            Google Fonts
            <span className="ml-1" style={{ color: sbMuted, opacity: 0.6 }}>({GOOGLE_FONTS_COUNT})</span>
          </p>
          {isGoogleActive && (
            <button
              type="button"
              onClick={() => update({ googleFont: null })}
              className="text-2xs cursor-pointer transition-colors"
              style={{ color: sbMuted }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = sbText; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = sbMuted; }}
            >
              Clear
            </button>
          )}
        </div>

        {/* Active font badge — rendered in the selected font */}
        {isGoogleActive && (
          <div
            className="flex h-7 items-center rounded px-2"
            style={{ border: `1px solid ${sbBorder}`, background: sbActive }}
          >
            <span
              className="truncate text-2xs font-medium"
              style={{ fontFamily: `"${state.googleFont}", sans-serif`, color: sbActiveText }}
            >
              {state.googleFont}
            </span>
          </div>
        )}

        {/* Command — search + scrollable grouped list */}
        <div
          className="overflow-hidden rounded"
          style={{ border: `1px solid ${sbBorder}`, background: "#ffffff" }}
        >
          <Command label="Google Fonts picker">
            <Command.Search>
              <Command.Input placeholder={`Search ${GOOGLE_FONTS_COUNT} fonts…`} />
            </Command.Search>
            <Command.List>
              <Command.Empty>No fonts found.</Command.Empty>
              {categoryOrder.map((cat) => {
                const fonts = byCategory.get(cat);
                if (!fonts?.length) return null;
                return (
                  <Command.Group key={cat} heading={cat}>
                    {fonts.map((font) => (
                      <Command.Item
                        key={font.family}
                        value={font.family}
                        onSelect={(v) => update({ googleFont: v, font: "theme" })}
                        className={cn(
                          state.googleFont === font.family &&
                            "bg-brand-soft text-brand-soft-text",
                        )}
                      >
                        <span style={{ fontFamily: `"${font.family}", sans-serif` }}>
                          {font.family}
                        </span>
                      </Command.Item>
                    ))}
                  </Command.Group>
                );
              })}
            </Command.List>
          </Command>
        </div>
      </div>
    </div>
  );
}

// ─── Component showcase ───────────────────────────────────────────────────────

function ComponentShowcase({ state }: { state: ThemeStudioState }): React.ReactElement {
  const useInline = state.inputLabelStyle === "inline";

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
          {useInline ? (
            <>
              <InlineField label="Full name" type="text" />
              <InlineField label="Email address" type="email" />
              <InlineField label="Required field" type="text" required description="Enter a value to continue." />
              <InlineField label="Invalid value" type="email" defaultValue="bad-email" invalid error="Invalid email format." />
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
            </>
          )}
          <div className="col-span-full">
            <SearchInput placeholder="Search components…" />
          </div>
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

/**
 * Build CSS custom-property overrides for the preview container.
 *
 * Root cause of the reactivity problem:
 * Tailwind 4 compiles `rounded-6` as `border-radius: var(--radius-6)` where
 * `--radius-6` is a Tailwind @theme var that lives on :root only and is NOT
 * inherited via the normal CSS cascade. Setting `--r-6` on a child div is not
 * enough — we must ALSO set the corresponding Tailwind bridge vars
 * (`--radius-2` … `--radius-12`) and spacing bridge vars (`--spacing-btn-sm`
 * etc.) directly on the preview container so that all `rounded-*`, `h-btn-*`,
 * and `h-input-*` utilities pick up the new values inside the preview area.
 *
 * The bridge mapping is defined in packages/ui/src/styles/index.css @theme:
 *   --radius-6 = var(--r-6)       → emit --radius-6
 *   --spacing-btn-sm = var(--control-h-sm) → emit --spacing-btn-sm, etc.
 *   --font-sans = var(--font-family-sans)  → emit --font-sans
 */
function buildThemeVars(state: ThemeStudioState): Record<string, string> {
  const vars: Record<string, string> = {};

  // ── Colors ─────────────────────────────────────────────────────────────────
  // Direct single-token overrides. Full palette ramp computation lives in
  // runtime.mjs; these are the most-visible single-var tokens that give
  // immediate feedback in the preview.
  if (state.brand   !== THEME_STUDIO_DEFAULTS.brand)   vars["--brand"]         = state.brand;
  if (state.accent  !== THEME_STUDIO_DEFAULTS.accent)  vars["--accent"]        = state.accent;
  if (state.canvas  !== THEME_STUDIO_DEFAULTS.canvas)  vars["--surface-canvas"] = state.canvas;
  if (state.surface !== THEME_STUDIO_DEFAULTS.surface) vars["--surface-sheet"] = state.surface;

  // ── Radius ─────────────────────────────────────────────────────────────────
  // Emit BOTH Angee token vars (--r-N) AND the Tailwind @theme bridge vars
  // (--radius-N) so rounded-* utilities respond inside the preview container.
  const RADIUS_MAP: Record<string, [string, string, string, string, string, string]> = {
    square:   ["0px",  "0px",  "0px",  "0px",  "0px",  "0px"],
    compact:  ["1px",  "2px",  "4px",  "6px",  "8px",  "10px"],
    standard: ["2px",  "4px",  "6px",  "8px",  "10px", "12px"],
    soft:     ["4px",  "6px",  "8px",  "10px", "12px", "14px"],
    round:    ["6px",  "8px",  "12px", "16px", "20px", "24px"],
    pill:     ["999px","999px","999px","999px","999px","999px"],
  };
  if (state.radius !== "theme") {
    const v = RADIUS_MAP[state.radius];
    if (v) {
      // Angee semantic tokens (used by runtime.mjs theme apply)
      vars["--r-2"]  = v[0]; vars["--r-4"]  = v[1]; vars["--r-6"]  = v[2];
      vars["--r-8"]  = v[3]; vars["--r-10"] = v[4]; vars["--r-12"] = v[5];
      // Tailwind @theme bridge vars (used by rounded-* utilities in CSS output)
      vars["--radius-2"]  = v[0]; vars["--radius-4"]  = v[1]; vars["--radius-6"]  = v[2];
      vars["--radius-8"]  = v[3]; vars["--radius-10"] = v[4]; vars["--radius-12"] = v[5];
      // Default --radius also used by some components
      vars["--radius"] = v[2];
    }
  }

  // ── Density ────────────────────────────────────────────────────────────────
  // The preview uses CSS `zoom` (see ThemeStudio component) to scale ALL
  // spacing uniformly. Here we only emit the Angee semantic control-height
  // tokens so that when this theme configuration is exported/applied via
  // AppearanceProvider, the real component heights reflect the density choice.
  //
  // Zoom-relative values: the Compact preset zooms to 0.72× so a "30px" button
  // at zoom 0.72 renders as ~22px — matching the design intent without
  // needing to recompile Tailwind utilities.
  const DENSITY_CONTROL_H: Record<string, [string, string, string]> = {
    compact:     ["24px", "28px", "32px"],
    balanced:    ["26px", "30px", "36px"],
    comfortable: ["30px", "36px", "44px"],
    spacious:    ["34px", "42px", "52px"],
  };
  const ZOOM_MAP: Record<string, number> = {
    compact:     0.72,
    balanced:    0.88,
    comfortable: 1.00,
    spacious:    1.32,
  };
  if (state.density !== "theme") {
    const v = DENSITY_CONTROL_H[state.density];
    if (v) {
      vars["--control-h-sm"] = v[0]!;
      vars["--control-h-md"] = v[1]!;
      vars["--control-h-lg"] = v[2]!;
    }
    const z = ZOOM_MAP[state.density];
    if (z !== undefined) {
      // Encode zoom as a CSS var so Apply can carry it to other stories.
      // preview.tsx reads --density-zoom and applies it as zoom on the story wrapper.
      vars["--density-zoom"] = String(z);
    }
  } else {
    vars["--density-zoom"] = "1";
  }

  // ── Font ───────────────────────────────────────────────────────────────────
  // Google Font takes priority over the preset selector.
  // Emit both the Angee token AND the Tailwind bridge var (--font-sans).
  const FONT_MAP: Record<string, string> = {
    system:     'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    inter:      'Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    humanist:   '"Avenir Next", "Segoe UI", system-ui, sans-serif',
    industrial: '"IBM Plex Sans", Arial, sans-serif',
    editorial:  'Georgia, "Times New Roman", serif',
    mono:       '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
  };

  let fontVal: string | undefined;
  if (state.googleFont) {
    // Google Font — wrap in quotes + system fallback
    fontVal = `"${state.googleFont}", system-ui, -apple-system, sans-serif`;
  } else {
    fontVal = FONT_MAP[state.font];
  }

  if (fontVal) {
    vars["--font-family-sans"] = fontVal;  // Angee token
    vars["--font-sans"]        = fontVal;  // Tailwind bridge var → font-sans utility
  }

  return vars;
}

// ─── Minimal inline SVG icons (no dependency on icon registry) ───────────────

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
