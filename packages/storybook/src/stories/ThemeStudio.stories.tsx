import type { Meta, StoryObj } from "@storybook/react-vite";
import { addons } from "storybook/preview-api";
import { ThemeStudio } from "@angee/ui";

/**
 * Theme Studio — interactive design-system configurator.
 *
 * Adjust typography, shape (border-radius), density, elevation, border weight,
 * and color tokens. Every change is reflected immediately in the component
 * showcase on the right.
 *
 * Press **Apply to all stories** to broadcast your current settings to every
 * story in the Storybook as CSS custom-property overrides. Navigate to any
 * component story and it will render with your chosen tokens.
 *
 * Controls available in the sidebar:
 * - **Colors** — brand, accent, neutral, canvas, surface, rail, status colors
 * - **Typography** — font family (System UI, Inter, Humanist, Industrial, Editorial, Mono)
 * - **Shape** — border-radius preset (Square → Round)
 * - **Density** — control height (Compact → Spacious)
 * - **Elevation** — shadow depth (Flat → Dramatic)
 * - **Borders** — border weight (Borderless → Strong)
 * - **Inputs** — label style (Stacked vs Floating)
 */
const meta = {
  title: "Foundations/Theme Studio",
  component: ThemeStudio,
  parameters: {
    layout: "fullscreen",
    docs: {
      description: {
        component:
          "Interactive design-system configurator. Adjust tokens and press Apply to broadcast them to every story.",
      },
    },
  },
} satisfies Meta<typeof ThemeStudio>;

export default meta;
type Story = StoryObj<typeof meta>;

const THEME_OVERRIDES_KEY = "angee:storybook-theme-overrides";

/**
 * Persists CSS-var overrides from ThemeStudio to two places:
 *  1. localStorage — read immediately by every new iframe at mount time,
 *     before any channel event arrives.
 *  2. Storybook globals via updateGlobals — updates the live current story
 *     in the same iframe without a page reload.
 */
function handleApply(cssVars: Record<string, string>) {
  // 1. Persist to localStorage so every subsequent story iframe reads it.
  try {
    localStorage.setItem(THEME_OVERRIDES_KEY, JSON.stringify(cssVars));
  } catch { /* quota — silent */ }

  // 2. Update live globals for the current story.
  try {
    addons.getChannel().emit("updateGlobals", { globals: { themeOverrides: cssVars } });
  } catch { /* channel unavailable in standalone — silent */ }
}

export const Default: Story = {
  render: () => <ThemeStudio onApply={handleApply} />,
};
