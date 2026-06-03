import type { Decorator, Preview } from "@storybook/react-vite";
import { withThemeByDataAttribute } from "@storybook/addon-themes";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import {
  AuthProvider,
  GraphQLClientProvider,
  AppRuntimeProvider,
  type AngeeUrqlClientOptions,
  type AppRuntime,
} from "@angee/sdk";
import { ToastProvider, baseIcons } from "@angee/base";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

import "../src/storybook.css";

const previewRuntime = {
  icons: baseIcons,
  menus: [
    { id: "notes", label: "Notes", to: "/notes", icon: "notes" },
    { id: "resources", label: "Resources", to: "/resources", icon: "archive" },
    { id: "iam", label: "IAM", to: "/iam", icon: "auth" },
    { id: "activity", label: "Activity", to: "/activity", icon: "activity" },
  ],
} satisfies Partial<AppRuntime>;

const previewAuth = {
  user: {
    id: "user_ada",
    name: "Ada Lovelace",
    username: "ada",
    email: "ada@example.com",
  },
  status: "authenticated" as const,
};

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
} satisfies Record<string, AngeeUrqlClientOptions>;

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
      <AuthProvider auth={previewAuth}>
        <GraphQLClientProvider config={previewSchemas} schema="public">
          <NuqsTestingAdapter>
            <ToastProvider>
              <div className="min-h-screen bg-canvas p-6 font-sans text-fg">
                <RouterProvider router={router} />
              </div>
            </ToastProvider>
          </NuqsTestingAdapter>
        </GraphQLClientProvider>
      </AuthProvider>
    </AppRuntimeProvider>
  );
};

const preview: Preview = {
  parameters: {
    layout: "padded",
    backgrounds: { disable: true },
    controls: { expanded: true, matchers: { color: /(background|color)$/i } },
    options: {
      storySort: {
        order: ["Tokens", "Primitives", "Chrome", "Widgets", "Toolbars", "Shell", "Scenes", "Reference"],
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
