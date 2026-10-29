import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute, type MenuDeclarations } from "@angee/app";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import { Glyph, Tab } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import {
  Activity,
  GitBranch,
  Layers3,
  Map,
  Package,
  Rocket,
} from "lucide-react";

import { enPortfolioMessages } from "./i18n";
import {
  projectPortfolioFormSection,
  taskReleaseFormSection,
} from "./portfolio-fields";
import { INITIATIVE_MODEL, PRODUCT_MODEL } from "./resources";
import {
  InitiativeUpdatesSection,
  ProjectUpdatesSection,
} from "./update-composer";
import { InitiativeProjectsTab, ProductProjectsTab, ProductReleasesTab } from "./record-sections";

/** A tab label resolved through the host's i18n, with the bundled English as the fallback. */
const tabLabel = (key: keyof typeof enPortfolioMessages) => ({ namespace: "portfolio", key, fallback: enPortfolioMessages[key] });


const portfolioRoutes: readonly BaseAddonRoute[] = [
  {
    name: "portfolio.roadmap",
    path: "/portfolio/roadmap",
    component: lazyRouteComponent(
      () => import("./views/RoadmapPage"),
      "RoadmapPage",
    ),
  },
  ...resourcePageRoutes(
    "portfolio.products",
    "/portfolio/products",
    lazyRouteComponent(
      () => import("./views/ProductsPage"),
      "ProductsPage",
    ),
    PRODUCT_MODEL,
  ),
  ...resourcePageRoutes(
    "portfolio.initiatives",
    "/portfolio/initiatives",
    lazyRouteComponent(
      () => import("./views/InitiativesPage"),
      "InitiativesPage",
    ),
    INITIATIVE_MODEL,
  ),
];

const portfolioMenu: MenuDeclarations = {
  "portfolio": { label: "Portfolio", icon: "portfolio" },
  "portfolio.roadmap": { label: "Roadmap", icon: "portfolio-roadmap", route: "portfolio.roadmap", parent: "portfolio" },
  "portfolio.products": { label: "Products", icon: "portfolio-product", route: "portfolio.products", parent: "portfolio" },
  "portfolio.initiatives": { label: "Initiatives", icon: "portfolio-initiative", route: "portfolio.initiatives", parent: "portfolio" },
};

const portfolio = defineBaseAddon({
  id: "portfolio",
  routes: portfolioRoutes,
  menus: portfolioMenu,
  i18n: { portfolio: enPortfolioMessages },
  containers: {
    [`${PROJECT_MODEL}#sections`]: {
      "portfolio.project-fields": { sequence: 40, content: projectPortfolioFormSection },
      "portfolio.project-updates": {
        sequence: 45,
        content: (
          <Tab id="portfolio-updates" label={tabLabel("update.pane.title")} icon={<Glyph decorative name="portfolio-update" />}>
            <ProjectUpdatesSection />
          </Tab>
        ),
      },
    },
    [`${PRODUCT_MODEL}#sections`]: {
      "portfolio.product-releases": {
        sequence: 10,
        content: <Tab id="releases" label={tabLabel("product.tabs.releases")} icon={<Glyph decorative name="portfolio-release" />}><ProductReleasesTab /></Tab>,
      },
      "portfolio.product-projects": {
        sequence: 20,
        content: <Tab id="projects" label={tabLabel("product.tabs.projects")} icon={<Glyph decorative name="projects" />}><ProductProjectsTab /></Tab>,
      },
    },
    [`${INITIATIVE_MODEL}#sections`]: {
      "portfolio.initiative-projects": {
        sequence: 10,
        content: <Tab id="projects" label={tabLabel("initiative.tabs.projects")} icon={<Glyph decorative name="projects" />}><InitiativeProjectsTab /></Tab>,
      },
      "portfolio.initiative-updates": {
        sequence: 45,
        content: (
          <Tab id="portfolio-updates" label={tabLabel("update.pane.title")} icon={<Glyph decorative name="portfolio-update" />}>
            <InitiativeUpdatesSection />
          </Tab>
        ),
      },
    },
    [`${TASK_MODEL}#sections`]: {
      "portfolio.task-release": { sequence: 45, content: taskReleaseFormSection },
    },
  },
  icons: {
    portfolio: Layers3,
    "portfolio-roadmap": Map,
    "portfolio-product": Package,
    "portfolio-initiative": GitBranch,
    "portfolio-release": Rocket,
    "portfolio-update": Activity,
  },
});

export {
  INITIATIVE_MODEL,
  INITIATIVE_PROJECT_MODEL,
  PRODUCT_MODEL,
  RELEASE_MODEL,
  UPDATE_MODEL,
} from "./resources";
export {
  PortfolioHealthSummary,
  PortfolioUpdateComposer,
  parsePortfolioUpdateValues,
} from "./update-composer";
export default portfolio;
