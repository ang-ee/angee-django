import {
  ModelMetadataProvider,
} from "@angee/metadata";
import type {
  Decorator,
  Preview } from "@storybook/react-vite";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import {
  AppearanceProvider,
  AppRuntimeProvider,
  defineThemeContribution,
  type AppRuntime,
  } from "@angee/ui";
import { themes as auroraThemes } from "@angee/theme-aurora/themes";
import { themes as brandThemes } from "@angee/theme-brand/themes";
import { themes as carbonThemes } from "@angee/theme-carbon/themes";
import { themes as midnightThemes } from "@angee/theme-midnight/themes";
import { themes as stockThemes } from "@angee/theme-stock/themes";
import { themes as warmRedThemes } from "@angee/theme-warm-red/themes";
import {
  ActiveGraphQLSchemaProvider,
} from "@angee/metadata";
import {
  createAngeeHasuraDataProviders,
  Refine,
  tanStackRouterProvider,
  type AngeeHasuraSchemaConfig,
  type ResourceProps,
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
import "@angee/theme-aurora/styles";

const previewThemes = [
  stockThemes[0],
  carbonThemes[0],
  auroraThemes[0],
  midnightThemes[0],
  warmRedThemes[0],
  brandThemes[0],
].map((definition) => defineThemeContribution({ definition }));

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
  themes: previewThemes,
} satisfies Partial<AppRuntime>;

const previewResources: ResourceProps[] = [
  previewMenuResource("notes", "Notes", "/notes", "notes"),
  previewMenuResource("resources", "Resources", "/resources", "archive"),
  previewMenuResource("iam", "Permissions", "/iam", "auth"),
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

const withAngeeProviders: Decorator = (Story, context) => {
  // Shell studies supply their own menu/route fixture without nesting a second
  // Refine or router root. Other stories keep the standard workshop context.
  const resources: ResourceProps[] = context.parameters.angeeResources ?? previewResources;
  const extraRoutes: string[] = context.parameters.angeeRoutes ?? [];
  const rootRoute = createRootRoute({
    component: () => (
      <AppRuntimeProvider runtime={previewRuntime}>
        <AppearanceProvider
          host={{
            themeId: typeof context.globals.themeId === "string"
              ? context.globals.themeId
              : "angee.stock",
            colorScheme: context.globals.colorScheme === "dark"
              ? "dark"
              : context.globals.colorScheme === "light"
                ? "light"
                : "system",
          }}
        >
          <Refine
            dataProvider={previewDataProviders}
            resources={resources}
            routerProvider={tanStackRouterProvider}
            options={{ syncWithLocation: false }}
          >
            <ActiveGraphQLSchemaProvider schema="public">
              <ModelMetadataProvider>
                <NuqsTestingAdapter>
                  <ToastProvider>
                    <Outlet />
                  </ToastProvider>
                </NuqsTestingAdapter>
              </ModelMetadataProvider>
            </ActiveGraphQLSchemaProvider>
          </Refine>
        </AppearanceProvider>
      </AppRuntimeProvider>
    ),
  });
  const routes = [...new Set<string>([...storybookRoutes, ...extraRoutes])].map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: Story,
    }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(routes),
    history: createMemoryHistory({
      initialEntries: [
        typeof context.parameters.route === "string"
          ? context.parameters.route
          : "/notes",
      ],
    }),
    defaultPreload: false,
  });

  return (
    <div className="min-h-screen bg-canvas p-6 font-sans text-fg">
      <RouterProvider router={router} />
    </div>
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
  globalTypes: {
    themeId: {
      name: "Theme",
      description: "Installed Angee theme",
      defaultValue: "angee.stock",
      toolbar: {
        icon: "paintbrush",
        items: [
          { value: "angee.stock", title: "Stock" },
          { value: "angee.carbon", title: "Carbon" },
          { value: "angee.aurora", title: "Aurora" },
          { value: "angee.midnight", title: "Midnight" },
          { value: "angee.warm-red", title: "Warm Red" },
          { value: "angee.brand", title: "Brand" },
        ],
        dynamicTitle: true,
      },
    },
    colorScheme: {
      name: "Color scheme",
      description: "Light, dark, or device scheme",
      defaultValue: "light",
      toolbar: {
        icon: "contrast",
        items: [
          { value: "light", title: "Light" },
          { value: "dark", title: "Dark" },
          { value: "system", title: "System" },
        ],
        dynamicTitle: true,
      },
    },
  },
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
  decorators: [withAngeeProviders],
};

export default preview;
