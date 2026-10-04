import type { Meta, StoryObj } from "@storybook/react-vite";
import { ThemeStudio } from "@angee/ui";

/**
 * Theme Studio — interactive design-system configurator.
 *
 * Adjust typography, shape (border-radius), density, elevation, border weight,
 * and color tokens. Every change is reflected immediately in the component
 * showcase on the right. Use this to validate a theme before shipping it.
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
    // ThemeStudio manages its own AppearanceProvider interaction via CSS vars,
    // so the Storybook toolbar theme/colorScheme globals still set the baseline
    // and the Studio layers its own overrides on top.
    docs: {
      description: {
        component:
          "Interactive design-system configurator. Adjust tokens and see all components update in real time.",
      },
    },
  },
} satisfies Meta<typeof ThemeStudio>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
