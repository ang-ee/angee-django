import {
  ModelMetadataProvider,
} from "@angee/metadata";
import type {
  Decorator,
  Preview } from "@storybook/react-vite";
import { withThemeByDataAttribute } from "@storybook/addon-themes";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import { Refine,
  type ResourceProps } from "@refinedev/core";
import {
  AppRuntimeProvider,
  type AppRuntime,
  } from "@angee/ui";
import {
  ActiveGraphQLSchemaProvider,
} from "@angee/metadata";
import {
  createAngeeHasuraDataProviders,
  tanStackRouterProvider,
  type AngeeHasuraSchemaConfig,
} from "@angee/refine";
import { ToastProvider, baseIcons } from "@angee/ui";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

import "../src/storybook.css";

// Stories read auth from the runtime (the ui-owned seam); no app-level auth
// provider is mounted in the preview.
const previewRuntime = {
  icons: baseIcons,
  auth: {
    user: {
      id: "user_ada",
      name: "Ada Lovelace",
      email: "ada@example.com",
    },
    status: "authenticated" as const,
    hasRole: () => true,
  },
} satisfies Partial<AppRuntime>;

const previewResources: ResourceProps[] = [
  previewMenuResource("notes", "Notes", "/notes", "notes"),
  previewMenuResource("resources", "Resources", "/resources", "archive"),
  previewMenuResource("iam", "IAM", "/iam", "auth"),
  previewMenuResource("activity", "Activity", "/activity", "activity"),
];

const previewSchemas = {
  public: {
    url: "/graphql/public/",
    fetch: async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = typeof init?.body === "string" ? init.body : "";
      const payload = body.includes("angeeLogout")
        ? { data: { logout: true } }
        : {
            data: {
              currentUser: {
                id: "user_ada",
                username: "ada",
                firstName: "Ada",
                lastName: "Lovelace",
                email: "ada@example.com",
                isStaff: true,
                isActive: true,
              },
            },
          };

      return new Response(JSON.stringify(payload), {
        headers: { "content-type": "application/json" },
      });
    },
  },
} satisfies Record<string, AngeeHasuraSchemaConfig>;

const previewDataProviders = createAngeeHasuraDataProviders(
  previewSchemas,
  "public",
);

const storybookRoutes = [
  "/",
  "/activity",
  "/archive",
  "/files",
  "/iam",
  "/login",
  "/notes",
  "/reports",
  "/resources",
  "/settings",
  "/settings/preferences",
] as const;

const withAngeeProviders: Decorator = (Story) => {
  const rootRoute = createRootRoute({ component: Outlet });
  const routes = storybookRoutes.map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: Story,
    }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(routes),
    history: createMemoryHistory({ initialEntries: ["/notes"] }),
    defaultPreload: false,
  });

  return (
    <AppRuntimeProvider runtime={previewRuntime}>
      <Refine
        dataProvider={previewDataProviders}
        resources={previewResources}
        routerProvider={tanStackRouterProvider}
        options={{ syncWithLocation: false }}
      >
        <ActiveGraphQLSchemaProvider schema="public">
          <ModelMetadataProvider>
            <NuqsTestingAdapter>
              <ToastProvider>
                <div className="min-h-screen bg-canvas p-6 font-sans text-fg">
                  <RouterProvider router={router} />
                </div>
              </ToastProvider>
            </NuqsTestingAdapter>
          </ModelMetadataProvider>
        </ActiveGraphQLSchemaProvider>
      </Refine>
    </AppRuntimeProvider>
  );
};

function previewMenuResource(
  id: string,
  label: string,
  list: string,
  icon: string,
): ResourceProps {
  return {
    name: `menu:${id}`,
    identifier: `menu:${id}`,
    list,
    meta: { menuId: id, label, icon },
  };
}

const preview: Preview = {
  parameters: {
    layout: "padded",
    backgrounds: { disable: true },
    controls: { expanded: true, matchers: { color: /(background|color)$/i } },
    options: {
      storySort: {
        // Page (the composition parts) before Layouts (the full-page
        // compositions built on them); Toast is folded into Primitives beside
        // Alert, so there is no longer a one-member Feedback group.
        order: [
          "Foundations",
          "Primitives",
          "Chrome",
          "Shell",
          "Page",
          "Layouts",
          "Views",
          "Forms",
          "Widgets",
          "Fragments",
        ],
      },
    },
  },
  decorators: [
    withThemeByDataAttribute({
      themes: { Light: "light", Dark: "dark" },
      defaultTheme: "Light",
      attributeName: "data-theme",
    }),
    withAngeeProviders,
  ],
};

export default preview;
