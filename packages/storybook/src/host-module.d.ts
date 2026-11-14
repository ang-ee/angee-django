declare module "virtual:angee-storybook-host" {
  export const composedAddons: readonly import("@angee/app").BaseAddon[];
  export function loadComposedSchemas(): Promise<import("@angee/app/create-app").CreateAppInput["schemas"]>;
}
