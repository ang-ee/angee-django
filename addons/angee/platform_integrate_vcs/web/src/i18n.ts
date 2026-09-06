import { createNamespaceT } from "@angee/ui";

export const enPlatformIntegrateVcsMessages: Record<string, string> = {
  "apps.actionFailed": "The action could not be completed.",
  "apps.add": "Add",
  "apps.adding": "Adding…",
  "apps.scan": "Scan",
  "apps.addSource": "Add source",
  "apps.addSource.title": "Add an addon source",
  "apps.addSource.description": "Inventory a repository on a VCS bridge and point a new addon source at it. Scan it to discover its addons.",
  "apps.addSource.bridge": "VCS bridge",
  "apps.addSource.bridgePlaceholder": "Select a bridge",
  "apps.addSource.repo": "Repository",
  "apps.addSource.repoPlaceholder": "owner/repo",
  "apps.addSource.selected": "Selected",
  "apps.addSource.ref": "Ref",
  "apps.addSource.refPlaceholder": "Default branch",
  "apps.addSource.path": "Path",
  "apps.addSource.pathPlaceholder": "Repository root",
  "apps.scan.title": "Scan addon sources",
  "apps.scan.description": "Re-enumerate a source to discover its addons into the marketplace.",
  "apps.scan.loading": "Loading sources…",
  "apps.scan.loadError": "Could not load addon sources.",
  "apps.scan.retry": "Retry",
  "apps.scan.empty": "No addon sources yet. Add one to get started.",
};

export const usePlatformIntegrateVcsT = createNamespaceT("platform", enPlatformIntegrateVcsMessages);
