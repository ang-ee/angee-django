import type { Preview } from "@storybook/react-vite";
import { withThemeByDataAttribute } from "@storybook/addon-themes";
import { AppRuntimeProvider, type AppRuntime } from "@angee/sdk";
import { baseIcons } from "@angee/base";

import "../src/storybook.css";

const previewRuntime = {
  icons: baseIcons,
} satisfies Partial<AppRuntime>;

const preview: Preview = {
  parameters: {
    layout: "padded",
    backgrounds: { disable: true },
    controls: { expanded: true, matchers: { color: /(background|color)$/i } },
    options: {
      storySort: {
        order: ["Tokens", "Primitives", "Chrome", "Widgets", "Toolbars", "Shell", "Scenes", "Reference"],
      },
    },
  },
  decorators: [
    withThemeByDataAttribute({
      themes: { Light: "light", Dark: "dark" },
      defaultTheme: "Light",
      attributeName: "data-theme",
    }),
    (Story) => (
      <AppRuntimeProvider runtime={previewRuntime}>
        <div className="min-h-screen bg-canvas p-6 font-sans text-fg">
          <Story />
        </div>
      </AppRuntimeProvider>
    ),
  ],
};

export default preview;
