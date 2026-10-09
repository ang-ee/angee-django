import { useLayoutEffect, useMemo, useRef, useSyncExternalStore } from "react";
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
import { themes as angeeThemes } from "@angee/theme-angee/themes";
import { themes as auroraThemes } from "@angee/theme-aurora/themes";
import { themes as carbonThemes } from "@angee/theme-carbon/themes";
import { themes as fyltrThemes } from "@angee/theme-fyltr/themes";
import { themes as ledgerThemes } from "@angee/theme-ledger/themes";
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

import { titleCase } from "@angee/ui/lib/titleCase";

import "../src/storybook.css";
import "@angee/theme-aurora/styles";

const previewThemes = [
  defineThemeContribution({ definition: stockThemes[0] }),
  defineThemeContribution({ definition: angeeThemes[0] }),
  defineThemeContribution({ definition: fyltrThemes[0] }),
  defineThemeContribution({ definition: ledgerThemes[0] }),
  defineThemeContribution({ definition: carbonThemes[0] }),
  defineThemeContribution({ definition: auroraThemes[0] }),
  defineThemeContribution({ definition: midnightThemes[0] }),
  defineThemeContribution({ definition: warmRedThemes[0] }),
];

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

type StoryRenderer = Parameters<Decorator>[0];
type StoryContext = Parameters<Decorator>[1];
type StorySnapshot = { Story: StoryRenderer; globals: StoryContext["globals"] };

const withAngeeProviders: Decorator = (Story, context) => {
  // Full application stories own their router and runtime.
  if (context.parameters.angeeOwnRuntime === true) return <Story />;
  return <StoryProviders key={context.id} Story={Story} context={context} />;
};

function StoryProviders({ Story, context }: { Story: StoryRenderer; context: StoryContext }) {
  const latest = useRef<StorySnapshot>({ Story, globals: context.globals });
  const store = useMemo(() => {
    const listeners = new Set<() => void>();
    return {
      subscribe(listener: () => void) {
        listeners.add(listener);
        return () => { listeners.delete(listener); };
      },
      getSnapshot: () => latest.current,
      publish(snapshot: StorySnapshot) {
        latest.current = snapshot;
        listeners.forEach((listener) => listener());
      },
    };
  }, []);
  useLayoutEffect(() => {
    if (latest.current.Story !== Story || latest.current.globals !== context.globals) {
      store.publish({ Story, globals: context.globals });
    }
  }, [Story, context.globals, store]);

  const resources: ResourceProps[] = context.parameters.angeeResources ?? previewResources;
  const paths = [...new Set<string>([...storybookRoutes, ...(context.parameters.angeeRoutes ?? [])])];
  const initialRoute = typeof context.parameters.route === "string" ? context.parameters.route : "/notes";
  const resourceKey = JSON.stringify(resources);
  const routeKey = JSON.stringify(paths);
  const router = useMemo(() => {
    function StoryContent() {
      const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
      // Invoke the current renderer inside a stable route component. Treating
      // the changing Story function as a component type would remount its state.
      return snapshot.Story();
    }
    function StoryRoot() {
      const { globals } = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
      return (
        <AppRuntimeProvider runtime={previewRuntime}>
          <AppearanceProvider host={{
            themeId: typeof globals.themeId === "string" ? globals.themeId : "angee.stock",
            colorScheme: globals.colorScheme === "dark" ? "dark" : globals.colorScheme === "light" ? "light" : "system",
          }}>
            <Refine dataProvider={previewDataProviders} resources={resources}
              routerProvider={tanStackRouterProvider} options={{ syncWithLocation: false }}>
              <ActiveGraphQLSchemaProvider schema="public">
                <ModelMetadataProvider>
                  <NuqsTestingAdapter>
                    <ToastProvider><Outlet /></ToastProvider>
                  </NuqsTestingAdapter>
                </ModelMetadataProvider>
              </ActiveGraphQLSchemaProvider>
            </Refine>
          </AppearanceProvider>
        </AppRuntimeProvider>
      );
    }
    const rootRoute = createRootRoute({ component: StoryRoot });
    return createRouter({
      routeTree: rootRoute.addChildren(paths.map((path) => createRoute({
        getParentRoute: () => rootRoute, path, component: StoryContent,
      }))),
      history: createMemoryHistory({ initialEntries: [initialRoute] }),
      defaultPreload: false,
    });
    // Serialized declaration keys keep equivalent parameter arrays from
    // reconstructing the router when args or toolbar globals change.
  }, [initialRoute, resourceKey, routeKey, store]);

  return (
    <div className="min-h-screen bg-canvas p-6 font-sans text-fg">
      <RouterProvider router={router} />
    </div>
  );
}

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
        items: previewThemes.map(({ definition }) => ({
          value: definition.id,
          title: titleCase(definition.id.replace(/^angee\./, "")),
        })),
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
