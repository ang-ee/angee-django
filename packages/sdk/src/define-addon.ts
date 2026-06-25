// Re-export shim. The addon-composition API (`defineAddon`/`composeAddons`/
// `merge*`/the addon manifest + contribution types) moved up into `@angee/app`;
// this preserves the `@angee/sdk` import surface for the addons + storybook that
// still import it from here. The contribution TYPES (owned by `@angee/ui/runtime`)
// re-export through the moved module's own re-export chain.
export * from "@angee/app/define-addon";
