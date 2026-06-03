import type { I18nResources } from "@angee/sdk";

// Section titles double as the labels for the Overview count tiles. The chrome
// renders the "Operator" menu and its section sub-nav from the addon manifest
// (`menus` in index.ts), so the navigation labels are not i18n keys here.
export const enOperatorBundle = {
  operator: {
    "section.overview.title": "Overview",
    "section.services.title": "Services",
    "section.workspaces.title": "Workspaces",
    "section.sources.title": "Sources",
    "section.gitops.title": "GitOps",
    "section.operations.title": "Operations",
    "section.templates.title": "Templates",
    "section.secrets.title": "Secrets",
  },
} satisfies I18nResources;
