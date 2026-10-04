import type { Meta, StoryObj } from "@storybook/react-vite";

/**
 * Spacing — density and border-radius tokens in @angee/ui.
 *
 * Shows how the three control-height tokens (--control-h-sm/md/lg) scale
 * across density presets, and how the six radius tokens (--r-2 through --r-12)
 * change across shape presets.
 */
const meta = {
  title: "Foundations/Spacing",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Density presets ──────────────────────────────────────────────────────────

const DENSITY_PRESETS: Array<{
  name: string;
  description: string;
  vars: React.CSSProperties;
}> = [
  {
    name: "Compact",
    description: "24 / 28 / 34px — maximum data density. Good for data tables and dense admin UIs.",
    vars: { "--control-h-sm": "24px", "--control-h-md": "28px", "--control-h-lg": "34px" } as React.CSSProperties,
  },
  {
    name: "Balanced",
    description: "26 / 32 / 38px — between compact and comfortable. Useful for developer tools.",
    vars: { "--control-h-sm": "26px", "--control-h-md": "32px", "--control-h-lg": "38px" } as React.CSSProperties,
  },
  {
    name: "Comfortable",
    description: "28 / 34 / 40px — the default. Suits most product UIs. Generous without being loose.",
    vars: { "--control-h-sm": "28px", "--control-h-md": "34px", "--control-h-lg": "40px" } as React.CSSProperties,
  },
  {
    name: "Spacious",
    description: "30 / 38 / 44px — breathing room. Landing pages, marketing, consumer-facing products.",
    vars: { "--control-h-sm": "30px", "--control-h-md": "38px", "--control-h-lg": "44px" } as React.CSSProperties,
  },
];

// ─── Radius presets ───────────────────────────────────────────────────────────

const RADIUS_PRESETS: Array<{
  name: string;
  description: string;
  vars: React.CSSProperties;
}> = [
  {
    name: "Square",
    description: "0px everywhere — sharp, utilitarian. IBM Carbon-style precision.",
    vars: { "--r-2": "0px", "--r-4": "0px", "--r-6": "0px", "--r-8": "0px", "--r-10": "0px", "--r-12": "0px" } as React.CSSProperties,
  },
  {
    name: "Compact",
    description: "1–10px — barely rounded. Structured and professional.",
    vars: { "--r-2": "1px", "--r-4": "2px", "--r-6": "4px", "--r-8": "6px", "--r-10": "8px", "--r-12": "10px" } as React.CSSProperties,
  },
  {
    name: "Standard",
    description: "2–12px — the default. Clean and modern without being bubbly.",
    vars: { "--r-2": "2px", "--r-4": "4px", "--r-6": "6px", "--r-8": "8px", "--r-10": "10px", "--r-12": "12px" } as React.CSSProperties,
  },
  {
    name: "Soft",
    description: "4–14px — slightly generous. Friendly and approachable.",
    vars: { "--r-2": "4px", "--r-4": "6px", "--r-6": "8px", "--r-8": "10px", "--r-10": "12px", "--r-12": "14px" } as React.CSSProperties,
  },
  {
    name: "Round",
    description: "6–24px — expressive curves. Consumer-facing, mobile-first products.",
    vars: { "--r-2": "6px", "--r-4": "8px", "--r-6": "12px", "--r-8": "16px", "--r-10": "20px", "--r-12": "24px" } as React.CSSProperties,
  },
];

// ─── Radius tokens ─────────────────────────────────────────────────────────────

const RADIUS_TOKENS: Array<{ token: string; tailwind: string; usage: string }> = [
  { token: "--r-2",   tailwind: "rounded-2",   usage: "Subtle rounding — avatars, small chips" },
  { token: "--r-4",   tailwind: "rounded-4",   usage: "Small components — tags, code blocks" },
  { token: "--r-6",   tailwind: "rounded-6",   usage: "Default — buttons, inputs, selects" },
  { token: "--r-8",   tailwind: "rounded-8",   usage: "Cards, modals, panels" },
  { token: "--r-10",  tailwind: "rounded-10",  usage: "Large surfaces, drawers" },
  { token: "--r-12",  tailwind: "rounded-12",  usage: "Full-height sheet surfaces" },
  { token: "--r-full",tailwind: "rounded-full","usage": "Pills, circular avatars, status dots" },
];

export const DensityScale: Story = {
  name: "Density presets",
  render: () => (
    <div className="min-h-screen bg-canvas p-8 text-fg">
      <header className="mb-8">
        <h1 className="text-22 font-semibold text-fg">Density presets</h1>
        <p className="mt-1 text-13 text-fg-muted">
          Four built-in density presets driven by three CSS custom properties:
          {" "}<code className="font-mono text-13">--control-h-sm/md/lg</code>.
          Every button, input, and select uses these tokens for its height.
        </p>
      </header>
      <div className="grid gap-8">
        {DENSITY_PRESETS.map(({ name, description, vars }) => (
          <section key={name} style={vars}>
            <div className="mb-3">
              <h2 className="text-15 font-semibold text-fg">{name}</h2>
              <p className="text-13 text-fg-muted">{description}</p>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              {/* Small */}
              <div className="flex flex-col items-center gap-1">
                <button
                  type="button"
                  className="inline-flex h-btn-sm cursor-pointer items-center rounded-6 border border-border-strong bg-inset px-3 text-xs font-medium text-fg transition-colors hover:bg-sheet"
                >
                  Small button
                </button>
                <span className="text-2xs text-fg-subtle">h-btn-sm</span>
              </div>
              {/* Medium */}
              <div className="flex flex-col items-center gap-1">
                <button
                  type="button"
                  className="inline-flex h-btn-md cursor-pointer items-center rounded-6 border border-brand bg-brand px-3 text-13 font-medium text-on-brand transition-colors hover:bg-brand-hover"
                >
                  Medium button
                </button>
                <span className="text-2xs text-fg-subtle">h-btn-md</span>
              </div>
              {/* Large */}
              <div className="flex flex-col items-center gap-1">
                <button
                  type="button"
                  className="inline-flex h-btn-lg cursor-pointer items-center rounded-6 border border-border-strong bg-sheet px-4 text-sm font-medium text-fg shadow-xs transition-colors hover:bg-inset"
                >
                  Large button
                </button>
                <span className="text-2xs text-fg-subtle">h-btn-lg</span>
              </div>
              {/* Input */}
              <div className="flex flex-col items-center gap-1">
                <input
                  type="text"
                  placeholder="Input field"
                  readOnly
                  className="h-input-h w-40 rounded-6 border border-border bg-sheet px-2 text-13 text-fg placeholder:text-fg-subtle outline-none"
                />
                <span className="text-2xs text-fg-subtle">h-input-h</span>
              </div>
            </div>
          </section>
        ))}
      </div>
    </div>
  ),
};

export const RadiusScale: Story = {
  name: "Border-radius presets",
  render: () => (
    <div className="min-h-screen bg-canvas p-8 text-fg">
      <header className="mb-8">
        <h1 className="text-22 font-semibold text-fg">Border-radius presets</h1>
        <p className="mt-1 text-13 text-fg-muted">
          Five presets map to six CSS custom properties (
          <code className="font-mono text-13">--r-2</code> through{" "}
          <code className="font-mono text-13">--r-12</code>). Every rounded utility
          (rounded-2, rounded-4 … rounded-12) references these vars.
        </p>
      </header>

      {/* Token reference */}
      <div className="mb-10 rounded-8 border border-border-subtle bg-sheet p-5">
        <h2 className="mb-4 text-13 font-semibold uppercase tracking-wide text-fg-muted">Token reference</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {RADIUS_TOKENS.map(({ token, tailwind, usage }) => (
            <div key={token} className="flex items-center gap-3">
              <span
                className="size-8 shrink-0 border border-border-subtle bg-inset"
                style={{ borderRadius: `var(${token})` }}
              />
              <div>
                <p className="text-2xs font-mono text-fg">{tailwind}</p>
                <p className="text-2xs text-fg-subtle">{usage}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Presets */}
      <div className="grid gap-8">
        {RADIUS_PRESETS.map(({ name, description, vars }) => (
          <section key={name} style={vars}>
            <div className="mb-3">
              <h2 className="text-15 font-semibold text-fg">{name}</h2>
              <p className="text-13 text-fg-muted">{description}</p>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              {/* Show a button, a card, a pill badge */}
              <button
                type="button"
                className="inline-flex h-btn-md cursor-pointer items-center rounded-6 border border-brand bg-brand px-3 text-13 font-medium text-on-brand transition-colors"
              >
                Button
              </button>
              <div className="h-14 w-32 rounded-8 border border-border-subtle bg-sheet shadow-xs" />
              <span className="inline-flex h-6 items-center rounded-full border border-brand bg-brand-soft px-3 text-2xs font-medium text-brand-soft-text">
                Pill badge
              </span>
              <input
                type="text"
                placeholder="Input"
                readOnly
                className="h-input-h w-32 rounded-6 border border-border bg-sheet px-2 text-13 text-fg placeholder:text-fg-subtle outline-none"
              />
              <div className="flex size-8 items-center justify-center rounded-full bg-brand text-on-brand text-xs font-semibold">
                AL
              </div>
            </div>
          </section>
        ))}
      </div>
    </div>
  ),
};
