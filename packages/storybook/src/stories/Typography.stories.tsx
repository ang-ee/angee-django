import type { Meta, StoryObj } from "@storybook/react-vite";

/**
 * The type scale: every text size, weight, and line-height token in @angee/ui.
 * Includes both the Tailwind-bridged custom steps (2xs, 13, 15, 22, 28, 34)
 * and the standard Tailwind utilities that map to the same CSS vars.
 * Switch the toolbar theme to see the font-family change per theme.
 */
const meta = {
  title: "Foundations/Typography",
  parameters: { layout: "fullscreen" },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

// ─── Type Scale ────────────────────────────────────────────────────────────────

const SCALE: Array<{
  label: string;
  className: string;
  weight?: string;
  sample: string;
  token: string;
}> = [
  { label: "Display",   className: "text-34", weight: "font-bold",     sample: "34px / Bold — The quick brown fox", token: "text-34 (2.125rem)" },
  { label: "H1",        className: "text-28", weight: "font-bold",     sample: "28px / Bold — The quick brown fox", token: "text-28 (1.75rem)" },
  { label: "H2",        className: "text-22", weight: "font-semibold", sample: "22px / Semibold — The quick brown fox", token: "text-22 (1.375rem)" },
  { label: "H3",        className: "text-lg",  weight: "font-semibold", sample: "18px / Semibold — The quick brown fox", token: "text-lg (1.125rem)" },
  { label: "H4",        className: "text-base", weight: "font-medium",  sample: "16px / Medium — The quick brown fox jumps over the lazy dog", token: "text-base (1rem)" },
  { label: "H5",        className: "text-15", weight: "font-medium",   sample: "15px / Medium — The quick brown fox jumps over the lazy dog", token: "text-15 (0.9375rem)" },
  { label: "Body",      className: "text-sm",  weight: "font-normal",  sample: "14px / Regular — The quick brown fox jumps over the lazy dog. Sphinx of black quartz, judge my vow.", token: "text-sm (0.875rem)" },
  { label: "Body S",    className: "text-13", weight: "font-normal",   sample: "13px / Regular — The quick brown fox jumps over the lazy dog. Sphinx of black quartz, judge my vow.", token: "text-13 (0.8125rem)" },
  { label: "Caption",   className: "text-xs",  weight: "font-normal",  sample: "12px / Regular — Labels, hints, meta information, timestamps, captions", token: "text-xs (0.75rem)" },
  { label: "Micro",     className: "text-2xs", weight: "font-medium",  sample: "11px / Medium — Eyebrows, table headers, status labels, nano captions", token: "text-2xs (0.6875rem)" },
];

const WEIGHTS = [
  { label: "Regular",  className: "font-normal",   value: "400" },
  { label: "Medium",   className: "font-medium",   value: "500" },
  { label: "Semibold", className: "font-semibold", value: "600" },
  { label: "Bold",     className: "font-bold",     value: "700" },
];

export const Scale: Story = {
  render: () => (
    <div className="min-h-screen bg-canvas p-8 text-fg">
      <header className="mb-8">
        <h1 className="text-22 font-semibold text-fg">Type scale</h1>
        <p className="mt-1 text-13 text-fg-muted">
          Switch the toolbar theme to see the font-family change. All sizes use
          CSS custom properties from <code className="text-13 font-mono text-fg">tokens.css</code>.
        </p>
      </header>
      <div className="divide-y divide-border-subtle rounded-8 border border-border-subtle bg-sheet">
        {SCALE.map(({ label, className, weight = "font-normal", sample, token }) => (
          <div key={label} className="flex items-baseline gap-6 px-6 py-4">
            <div className="w-16 shrink-0 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
              {label}
            </div>
            <p className={`${className} ${weight} min-w-0 flex-1 text-fg`}>
              {sample}
            </p>
            <code className="shrink-0 text-2xs font-mono text-fg-subtle">{token}</code>
          </div>
        ))}
      </div>
    </div>
  ),
};

export const Weights: Story = {
  render: () => (
    <div className="min-h-screen bg-canvas p-8 text-fg">
      <header className="mb-8">
        <h1 className="text-22 font-semibold text-fg">Font weights</h1>
        <p className="mt-1 text-13 text-fg-muted">
          Four weight steps — 400, 500, 600, 700.
        </p>
      </header>
      <div className="divide-y divide-border-subtle rounded-8 border border-border-subtle bg-sheet">
        {WEIGHTS.map(({ label, className, value }) => (
          <div key={label} className="flex items-baseline gap-6 px-6 py-5">
            <div className="w-20 shrink-0 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
              {label}
            </div>
            <p className={`text-22 ${className} flex-1 text-fg`}>
              The quick brown fox jumps over the lazy dog
            </p>
            <code className="shrink-0 text-2xs font-mono text-fg-subtle">{value}</code>
          </div>
        ))}
      </div>
    </div>
  ),
};

export const TextColors: Story = {
  render: () => (
    <div className="min-h-screen bg-canvas p-8 text-fg">
      <header className="mb-8">
        <h1 className="text-22 font-semibold text-fg">Text color tokens</h1>
        <p className="mt-1 text-13 text-fg-muted">
          The semantic text palette — use these to express hierarchy, not raw grays.
        </p>
      </header>
      <div className="grid gap-3">
        {[
          { token: "text-fg",      label: "Primary",   desc: "Main content, headings, labels" },
          { token: "text-fg-2",    label: "Secondary",  desc: "Supporting text, less prominent labels" },
          { token: "text-fg-muted",label: "Muted",      desc: "Helper text, descriptions, placeholders" },
          { token: "text-fg-subtle",label: "Subtle",    desc: "Timestamps, captions, disabled hints" },
          { token: "text-link",    label: "Link",        desc: "Clickable text and hyperlinks" },
          { token: "text-brand-soft-text", label: "Brand soft", desc: "Text on soft brand surfaces" },
          { token: "text-success-text",  label: "Success", desc: "Success state text" },
          { token: "text-warning-text",  label: "Warning", desc: "Warning state text" },
          { token: "text-danger-text",   label: "Danger",  desc: "Error / danger state text" },
        ].map(({ token, label, desc }) => (
          <div key={token} className="flex items-baseline gap-4 rounded-6 border border-border-subtle bg-sheet px-4 py-3">
            <p className={`text-15 font-semibold ${token} w-28 shrink-0`}>{label}</p>
            <p className={`text-sm ${token} flex-1`}>{desc}</p>
            <code className="text-2xs font-mono text-fg-subtle">{token}</code>
          </div>
        ))}
      </div>
    </div>
  ),
};
