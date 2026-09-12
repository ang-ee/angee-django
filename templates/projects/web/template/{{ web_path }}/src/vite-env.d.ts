/// <reference types="vite/client" />

declare module "*.graphql?raw" {
  const content: string;
  export default content;
}

declare module "virtual:angee-appearance" {
  import type { HostAppearanceDefaults } from "@angee/ui/theme";
  export const appearance: HostAppearanceDefaults;
  export default appearance;
}
