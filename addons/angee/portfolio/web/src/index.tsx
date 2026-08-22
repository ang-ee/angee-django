import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "@angee/app";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import { formViewSectionsSlot, type BaseMenuItem } from "@angee/ui";
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

const portfolioMenu: readonly BaseMenuItem[] = [
  {
    id: "portfolio",
    label: "Portfolio",
    icon: "portfolio",
    children: [
      {
        id: "portfolio.roadmap",
        label: "Roadmap",
        icon: "portfolio-roadmap",
        route: "portfolio.roadmap",
      },
      {
        id: "portfolio.products",
        label: "Products",
        icon: "portfolio-product",
        route: "portfolio.products",
      },
      {
        id: "portfolio.initiatives",
        label: "Initiatives",
        icon: "portfolio-initiative",
        route: "portfolio.initiatives",
      },
    ],
  },
];

const portfolio = defineBaseAddon({
  id: "portfolio",
  routes: portfolioRoutes,
  menus: portfolioMenu,
  i18n: { portfolio: enPortfolioMessages },
  slots: [
    {
      ...formViewSectionsSlot(PROJECT_MODEL),
      id: "portfolio.project-fields",
      sequence: 40,
      content: projectPortfolioFormSection,
    },
    {
      ...formViewSectionsSlot(PROJECT_MODEL),
      id: "portfolio.project-updates",
      sequence: 45,
      content: <ProjectUpdatesSection />,
    },
    {
      ...formViewSectionsSlot(INITIATIVE_MODEL),
      id: "portfolio.initiative-updates",
      sequence: 45,
      content: <InitiativeUpdatesSection />,
    },
    {
      ...formViewSectionsSlot(TASK_MODEL),
      id: "portfolio.task-release",
      sequence: 45,
      content: taskReleaseFormSection,
    },
  ],
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
