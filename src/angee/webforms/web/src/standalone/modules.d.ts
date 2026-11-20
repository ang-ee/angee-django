// Modules the bundler supplies to the standalone page. These declarations only
// satisfy `tsc`: the fragment's compiler loads no ambient Vite types (`types: []`).

// Side-effect stylesheets: SurveyJS's own and the token bridge.
declare module "*.css";

// SurveyJS's interface strings; each module registers its locale when it loads and ships no types.
declare module "survey-core/i18n/*";

// The host's appearance defaults, provided by `@angee/app/vite`.
declare module "virtual:angee-appearance" {
  import type { HostAppearanceDefaults } from "@angee/ui/theme";
  export const appearance: HostAppearanceDefaults;
}
