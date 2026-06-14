import type { Meta, StoryObj } from "@storybook/react-vite";
import { FILLS, TONES, toneClass } from "@angee/base";

/**
 * The semantic-color system: two orthogonal axes — `tone` (the palette) ×
 * `variant` (the fill). Every cell below is `toneClass(tone, fill)`, the single
 * matrix owner in `@angee/base`, so this page can never drift from the code or
 * the design tokens. Toggle the theme in the toolbar to see both light and dark.
 */
const meta = {
  title: "Foundations/Colors",
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

const Swatch = ({ tone, fill }: { tone: (typeof TONES)[number]; fill: (typeof FILLS)[number] }) => (
  <span
    className={`inline-flex h-9 w-full items-center justify-center rounded-md border px-3 text-13 font-medium ${toneClass(
      tone,
      fill,
    )}`}
  >
    Aa
  </span>
);

export const ToneFillMatrix: Story = {
  render: () => (
    <div className="min-h-screen bg-canvas p-6 text-fg">
      <h1 className="mb-1 text-lg font-semibold">tone × variant</h1>
      <p className="mb-5 max-w-prose text-13 text-fg-muted">
        Rows are the {TONES.length} tones; columns are the {FILLS.length} fills.
        Pick one of each — a status pill takes both, a single-tone primitive just
        a fill.
      </p>
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-2">
          <thead>
            <tr>
              <th className="px-2 text-left text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
                tone \ variant
              </th>
              {FILLS.map((fill) => (
                <th
                  key={fill}
                  className="px-2 text-left text-2xs font-semibold uppercase tracking-wide text-fg-subtle"
                >
                  {fill}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {TONES.map((tone) => (
              <tr key={tone}>
                <th className="whitespace-nowrap px-2 text-left text-13 font-medium text-fg-2">
                  {tone}
                </th>
                {FILLS.map((fill) => (
                  <td key={fill} className="w-28">
                    <Swatch tone={tone} fill={fill} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  ),
};

export const Tones: Story = {
  name: "Tones (soft)",
  render: () => (
    <div className="flex min-h-screen flex-wrap content-start gap-2 bg-canvas p-6">
      {TONES.map((tone) => (
        <span
          key={tone}
          className={`inline-flex h-8 items-center rounded-full border px-3 text-13 font-medium ${toneClass(
            tone,
            "soft",
          )}`}
        >
          {tone}
        </span>
      ))}
    </div>
  ),
};

export const Fills: Story = {
  render: () => (
    <div className="min-h-screen space-y-4 bg-canvas p-6">
      {(["brand", "danger", "neutral"] as const).map((tone) => (
        <div key={tone} className="flex flex-wrap items-center gap-2">
          <span className="w-16 text-13 font-medium text-fg-2">{tone}</span>
          {FILLS.map((fill) => (
            <span
              key={fill}
              className={`inline-flex h-9 items-center rounded-md border px-4 text-13 font-medium ${toneClass(
                tone,
                fill,
              )}`}
            >
              {fill}
            </span>
          ))}
        </div>
      ))}
    </div>
  ),
};
