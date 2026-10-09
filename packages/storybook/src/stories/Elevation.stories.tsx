import type { Meta, StoryObj } from "@storybook/react-vite";

/**
 * Elevation — the five shadow tokens in @angee/ui.
 *
 * Shows all five levels (xs → lg + popover) in every preset
 * (theme default, flat, subtle, soft, dramatic). Switch between
 * light and dark in the toolbar to see how shadows adapt.
 */
const meta = {
  title: "Foundations/Elevation",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const LEVELS: Array<{ name: string; className: string; token: string; usage: string }> = [
  { name: "xs",      className: "shadow-xs",      token: "--elevation-xs",      usage: "Resting cards, list rows" },
  { name: "sm",      className: "shadow-sm",      token: "--elevation-sm",      usage: "Hovered cards, dropdowns" },
  { name: "md",      className: "shadow-md",      token: "--elevation-md",      usage: "Drawers, panels, modals" },
  { name: "lg",      className: "shadow-lg",      token: "--elevation-lg",      usage: "Full-screen dialogs" },
  { name: "popover", className: "shadow-popover", token: "--elevation-popover", usage: "Tooltips, command palettes" },
];

// Inline style overrides that match the ELEVATION_TOKENS from runtime.mjs
const PRESETS: Array<{
  name: string;
  description: string;
  light: Record<string, string>;
}> = [
  {
    name: "Theme default",
    description: "The baseline elevation from tokens.css. Controlled by the active theme.",
    light: {},
  },
  {
    name: "Flat",
    description: "No drop shadows. Elevation is implied by border or background contrast only.",
    light: {
      "--elevation-xs":      "none",
      "--elevation-sm":      "none",
      "--elevation-md":      "0 0 0 1px #00000014",
      "--elevation-lg":      "0 0 0 1px #0000001f",
      "--elevation-popover": "0 0 0 1px #00000029",
    },
  },
  {
    name: "Subtle",
    description: "Light, whisper-thin shadows — airy and clean. Good for content-dense UIs.",
    light: {
      "--elevation-xs":      "0 1px 1px #0000000a",
      "--elevation-sm":      "0 1px 2px #0000000f",
      "--elevation-md":      "0 4px 12px #00000014",
      "--elevation-lg":      "0 12px 32px #0000001f",
      "--elevation-popover": "0 6px 16px #0000001a",
    },
  },
  {
    name: "Soft",
    description: "Noticeably wider spread — depth is visible without being dramatic.",
    light: {
      "--elevation-xs":      "0 1px 2px #0000000a",
      "--elevation-sm":      "0 2px 5px #00000012",
      "--elevation-md":      "0 8px 24px #0000001a",
      "--elevation-lg":      "0 20px 48px #00000024",
      "--elevation-popover": "0 12px 32px #0000001f",
    },
  },
  {
    name: "Dramatic",
    description: "High-contrast, expressive depth. Use for hero surfaces or strong visual hierarchy.",
    light: {
      "--elevation-xs":      "0 2px 4px #00000012",
      "--elevation-sm":      "0 4px 10px #0000001a",
      "--elevation-md":      "0 14px 36px #00000029",
      "--elevation-lg":      "0 28px 64px #00000038",
      "--elevation-popover": "0 18px 44px #00000033",
    },
  },
];

export const AllLevels: Story = {
  name: "All levels",
  render: () => (
    <div className="min-h-screen bg-canvas p-8 text-fg">
      <header className="mb-8">
        <h1 className="text-22 font-semibold text-fg">Elevation</h1>
        <p className="mt-1 text-13 text-fg-muted">
          Five shadow tokens mapped to <code className="font-mono text-13">shadow-*</code> utilities.
          Switch dark mode in the toolbar to see the adjusted dark-theme shadows.
        </p>
      </header>
      <div className="grid gap-5 sm:grid-cols-5">
        {LEVELS.map(({ name, className, token, usage }) => (
          <div key={name} className="flex flex-col items-center gap-4">
            <div
              className={`flex h-24 w-full items-center justify-center rounded-8 bg-sheet ${className}`}
            >
              <span className="text-lg font-semibold text-fg-muted">{name}</span>
            </div>
            <div className="text-center">
              <p className="text-13 font-medium text-fg">{name}</p>
              <code className="text-2xs font-mono text-fg-subtle">{token}</code>
              <p className="mt-0.5 text-2xs text-fg-subtle">{usage}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  ),
};

export const Presets: Story = {
  render: () => (
    <div className="min-h-screen bg-canvas p-8 text-fg">
      <header className="mb-8">
        <h1 className="text-22 font-semibold text-fg">Elevation presets</h1>
        <p className="mt-1 text-13 text-fg-muted">
          Five built-in presets. Select one in the theme customization editor to
          swap the complete shadow set in one step.
        </p>
      </header>
      <div className="grid gap-10">
        {PRESETS.map(({ name, description, light }) => (
          <section key={name} style={light as React.CSSProperties}>
            <div className="mb-4">
              <h2 className="text-15 font-semibold text-fg">{name}</h2>
              <p className="text-13 text-fg-muted">{description}</p>
            </div>
            <div className="grid gap-4 sm:grid-cols-5">
              {LEVELS.map(({ name: levelName, className, token }) => (
                <div key={levelName} className="flex flex-col items-center gap-3">
                  <div
                    className={`flex h-20 w-full items-center justify-center rounded-8 bg-sheet ${className}`}
                  >
                    <span className="text-sm font-medium text-fg-muted">{levelName}</span>
                  </div>
                  <code className="text-2xs font-mono text-fg-subtle">{token}</code>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  ),
};
