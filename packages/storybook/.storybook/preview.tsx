import { useMemo, useSyncExternalStore } from "react";
import type { Decorator, Preview } from "@storybook/react-vite";
import type { AppRuntime } from "@angee/ui";
import { parseFlatSearch, stringifyFlatSearch } from "@angee/app/create-app";
import type { AngeeHasuraSchemaConfig, ResourceProps } from "@angee/refine";
import { RuntimeFixture, storyAppearance, useStorySnapshot, type RuntimeFixtureProps } from "../src/stories/runtime-fixtures";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

import { titleCase } from "@angee/ui/lib/titleCase";
import { previewThemes, themeGlobalValue } from "../src/theme-global";

import "../src/storybook.css";
import "@angee/theme-aurora/styles";

// Stories read auth from the runtime (the ui-owned seam); no app-level auth
// provider is mounted in the preview.
const previewRuntime = {
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
type StoryFixture = Partial<Omit<RuntimeFixtureProps, "children" | "appearance">>;
type StorySnapshot = { Story: StoryRenderer; globals: StoryContext["globals"]; fixture: StoryFixture };

const withAngeeProviders: Decorator = (Story, context) => {
  // Direct fixture stories use this same canonical stack inside their router.
  // Their render functions pass toolbar globals through storyAppearance.
  if (context.parameters.angeeOwnRuntime === true) return <DirectFixtureStory key={context.id} Story={Story} />;
  return <StoryProviders key={context.id} Story={Story} context={context} />;
};

function DirectFixtureStory({ Story }: { Story: StoryRenderer }) {
  return Story();
}

function StoryProviders({ Story, context }: { Story: StoryRenderer; context: StoryContext }) {
  const declaration = context.parameters.angeeFixture as StoryFixture | (() => StoryFixture) | undefined;
  const fixture = useMemo(() => typeof declaration === "function" ? declaration() : declaration ?? {}, [declaration]);
  const store = useStorySnapshot<StorySnapshot>({ Story, globals: context.globals, fixture });

  const paths = [...new Set<string>([...storybookRoutes, ...(context.parameters.angeeRoutes ?? [])])];
  const initialRoute = typeof context.parameters.route === "string" ? context.parameters.route : "/notes";
  const routeKey = JSON.stringify(paths);
  const router = useMemo(() => {
    function StoryContent() {
      const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
      // Invoke the current renderer inside a stable route component. Treating
      // the changing Story function as a component type would remount its state.
      return snapshot.Story();
    }
    function StoryRoot() {
      const { globals, fixture } = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
      return <RuntimeFixture
        {...fixture}
        schemas={fixture.schemas ?? previewSchemas}
        resources={fixture.resources ?? (fixture.schemas ? undefined : previewResources)}
        runtime={{ ...previewRuntime, ...fixture.runtime }}
        appearance={storyAppearance(globals)}
        routed
      ><Outlet /></RuntimeFixture>;
    }
    const rootRoute = createRootRoute({ component: StoryRoot });
    return createRouter({
      routeTree: rootRoute.addChildren(paths.map((path) => createRoute({
        getParentRoute: () => rootRoute, path, component: StoryContent,
      }))),
      history: createMemoryHistory({ initialEntries: [initialRoute] }),
      parseSearch: parseFlatSearch,
      stringifySearch: stringifyFlatSearch,
      defaultPreload: false,
    });
    // Serialized route keys keep equivalent parameter arrays from
    // reconstructing the router when args or toolbar globals change.
  }, [initialRoute, routeKey, store]);

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
      defaultValue: themeGlobalValue("angee.stock"),
      toolbar: {
        icon: "paintbrush",
        items: previewThemes.map(({ definition }) => ({
          value: themeGlobalValue(definition.id),
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
          "Library",
          "Foundations",
          "Primitives",
          "Chrome",
          "Page",
          "Layouts",
          "Views",
          "Forms",
          "Widgets",
          "Fragments",
          "Reports",
          "*",
          "Explorations",
        ],
      },
    },
  },
  decorators: [withAngeeProviders],
};

export default preview;
