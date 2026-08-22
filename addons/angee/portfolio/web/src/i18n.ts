import { createNamespaceT } from "@angee/ui";

export const enPortfolioMessages: Record<string, string> = {
  "common.body": "Details",
  "common.health": "Health",
  "common.name": "Name",
  "common.order": "Order",
  "common.owner": "Owner",
  "common.priority": "Priority",
  "common.project": "Project",
  "common.status": "Status",
  "common.targetDate": "Target date",
  "initiative.empty.projects": "No projects are linked to this initiative.",
  "initiative.group.delivery": "Delivery",
  "initiative.group.identity": "Initiative",
  "initiative.tabs.projects": "Projects",
  "initiative.tree.description":
    "Strategic intent grouped by parent initiative; delivery remains in projects.",
  "product.empty.projects": "No projects are linked to this product.",
  "product.empty.releases": "No releases have been recorded for this product.",
  "product.group.identity": "Product",
  "product.group.provenance": "Stewardship and provenance",
  "product.personalLabel": "Area",
  "product.tabs.projects": "Projects",
  "product.tabs.releases": "Releases",
  "release.fixedIn": "Fixed in",
  "release.fixedIn.description": "The release that contains this task, for example 2.3.",
  "release.group.dates": "Release dates",
  "roadmap.description":
    "Bounded pushes grouped by their long-lived product and strategic initiative.",
  "roadmap.initiatives.description":
    "Initiative placements show which push advances each branch of strategy.",
  "roadmap.initiatives.title": "By initiative",
  "roadmap.products.description":
    "Product groups collect the projects that change a long-lived subject.",
  "roadmap.products.title": "By product",
  "roadmap.title": "Roadmap",
  "update.body": "Update",
  "update.button": "Report update",
  "update.description": "Assert current health and record the supporting narrative.",
  "update.error": "The portfolio update could not be reported.",
  "update.health.atRisk": "At risk",
  "update.health.none": "No health update yet",
  "update.health.offTrack": "Off track",
  "update.health.onTrack": "On track",
  "update.health.updated": "Updated",
  "update.pane.description":
    "Health is written only by reports and copied onto this record for planning views.",
  "update.pane.title": "Health updates",
  "update.submit": "Report update",
  "update.submitting": "Reporting…",
  "update.title": "Report health",
};

export const usePortfolioT = createNamespaceT("portfolio", enPortfolioMessages);
export type PortfolioT = ReturnType<typeof usePortfolioT>;
