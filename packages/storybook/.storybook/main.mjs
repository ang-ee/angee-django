// @ts-check

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");

/** @type {import("@storybook/react-vite").StorybookConfig} */
const config = {
  framework: "@storybook/react-vite",
  stories: ["../src/stories/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-themes"],
  typescript: { reactDocgen: false },
  viteFinal: async (vite) => {
    const tailwind = (await import("@tailwindcss/vite")).default;
    vite.plugins = [...(vite.plugins ?? []), tailwind()];
    vite.resolve = {
      ...(vite.resolve ?? {}),
      preserveSymlinks: true,
      alias: {
        ...(vite.resolve?.alias ?? {}),
        react: join(ROOT, "node_modules/react"),
        "react-dom": join(ROOT, "node_modules/react-dom"),
        "react-dom/client": join(ROOT, "node_modules/react-dom/client"),
        "react/jsx-runtime": join(ROOT, "node_modules/react/jsx-runtime"),
      },
    };
    return vite;
  },
};

export default config;
