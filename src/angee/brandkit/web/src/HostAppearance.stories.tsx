import { useMemo } from "react";
import { defineBaseAddon, type BaseAddon } from "@angee/app";
import { LoginPage } from "@angee/app/auth";
import type { CreateAppInput } from "@angee/app/create-app";
import { operationDocuments } from "@angee/gql/console/actions";
import { ComposedRuntimeFixture, storyAppearance, storySchema, type GraphQLWireRequest } from "@angee/storybook/testing";
import stock from "@angee/theme-stock";
import { ConsoleLayout } from "@angee/ui";

import brandkit from "./index";
import {
  ANGEE, HARBOUR, ORCHARD, PALE, brandKitResource, brandKitWire, defaultKitColors, type KitRow,
} from "./views/brand-kits.test-support";

/**
 * The production `createApp`, frame and hold included, as a host composes it. Two things differ
 * from a host page: the workshop has no head bootstrap script and no boot cache, so every story load
 * is a device's first visit. The page renders under the workshop's own stock tokens while the kit is
 * read, then the root takes the kit's, and controls cross-fade for `--dur-base` (their
 * `transition-colors`). A host's first visit paints the build's appearance first in the same way; its
 * next visits paint the cached kit before the app mounts and show no change.
 */
export default {
  title: "Brand kits/Host appearance",
  parameters: { angeeOwnRuntime: true, layout: "fullscreen" },
  excludeStories: ["HostAppearanceStory"],
};

type StoryContext = { globals: Record<string, unknown> };
const scheme = ({ globals }: StoryContext) => storyAppearance(globals).colorScheme;

/** Each deployment's kits, the default first; one value per story, so the wire keeps its saves across renders. */
const ORCHARD_KITS: readonly KitRow[] = [ORCHARD, HARBOUR, { ...ANGEE, is_default: false }];
const HARBOUR_KITS: readonly KitRow[] = [{ ...HARBOUR, is_default: true }, { ...ORCHARD, is_default: false }];
const PALE_KITS: readonly KitRow[] = [PALE];
const NO_KITS: readonly KitRow[] = [];

/** The console in Orchard, the default kit: rail, canvas, sheets, buttons, links, status chips and the focus tokens in its colours; stock's font, corners, density, elevation and mark. */
export const ConsoleOrchard = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={ORCHARD_KITS} colorScheme={scheme(context)} /> };
/** The console in Harbour, the default kit. */
export const ConsoleHarbour = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={HARBOUR_KITS} colorScheme={scheme(context)} /> };
/** No default kit: the console wears the stock theme exactly. */
export const ConsoleNoKit = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={NO_KITS} colorScheme={scheme(context)} /> };
/** Orchard is the default. Harbour's form is open: tick Default kit and save, and the console turns Harbour without a reload; the list shows Harbour first. */
export const SaveTheDefault = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={ORCHARD_KITS} colorScheme={scheme(context)} initialEntry="/brand-kits/bkt_harbour" /> };
/** A brand too pale for white ink: buttons fill pale with dark ink, links darken to read, and the console shows no warning. */
export const PaleKit = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={PALE_KITS} colorScheme={scheme(context)} /> };
/**
 * The sign-in page, signed out, in Orchard. The stories' host declares no brand, so the page shows the
 * framework's own hero, whose backdrop is fixed artwork the kit does not reach. The button, the inputs,
 * the text and the card's fill (the sheet at 94 %) take Orchard's colours; the name and mark are the
 * framework's.
 */
export const SignInOrchard = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={ORCHARD_KITS} signedIn={false} initialEntry="/login" colorScheme={scheme(context)} /> };
/** The sign-in page, signed out, in Harbour, as SignInOrchard. */
export const SignInHarbour = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={HARBOUR_KITS} signedIn={false} initialEntry="/login" colorScheme={scheme(context)} /> };
/** The sign-in page, signed out, with no default kit: the stock theme over the framework's hero. */
export const SignInNoKit = { render: (_args: unknown, context: StoryContext) => <HostAppearanceStory kits={NO_KITS} signedIn={false} initialEntry="/login" colorScheme={scheme(context)} /> };

/** A platform admin: the identity the console's Brand kits entry asks for. */
const ADMIN = {
  id: "user-1", username: "ada", firstName: "Ada", lastName: "Lovelace", email: "ada@example.test",
  isStaff: false, isActive: true, hasUsablePassword: true, preferences: {}, roleRefs: [], permitted: ["brandkit.BrandKit#create"],
};

const STOCK_ONLY: readonly BaseAddon[] = [stock];

/** The host's own sign-in route, as a project's `main.tsx` declares it. */
const signIn = defineBaseAddon({
  id: "host-auth",
  routes: [{ name: "auth.login", path: "/login", layout: "public", component: () => <LoginPage methods={null} /> }],
});

/**
 * The production `createApp` over two wire fakes, with the stock theme, the sign-in route and the
 * real brand kit manifest: the default kit's colours come only from the public schema's
 * `default_brand_kit_colors`, which answers the kit the console's saves leave the default. It asks
 * the composed fixture for the host appearance source, which a workshop otherwise leaves out.
 */
export function HostAppearanceStory({
  kits,
  signedIn = true,
  initialEntry = "/brand-kits",
  colorScheme = "light",
  themeId = "angee.stock",
  themes = STOCK_ONLY,
  failKitRead = false,
  kitReadGate,
  onRequest,
}: {
  kits: readonly KitRow[];
  signedIn?: boolean;
  initialEntry?: string;
  colorScheme?: "light" | "dark" | "system";
  themeId?: string;
  /** The theme addons installed, stock by default. */
  themes?: readonly BaseAddon[];
  /** The public default-kit read's first attempt answers an HTTP 500, as a failing server would; every retry stays unanswered. */
  failKitRead?: boolean;
  /** The public default-kit read answers only once this settles, as a slow server would. */
  kitReadGate?: Promise<void>;
  /** Every request, with the schema it went to, in the order they were sent. */
  onRequest?: (schema: "public" | "console", request: GraphQLWireRequest) => void;
}) {
  const input = useMemo<CreateAppInput>(() => {
    const wire = brandKitWire(kits);
    const user = signedIn ? ADMIN : null;
    const publicFetch = storySchema({
      current_user: () => user,
      default_brand_kit_colors: async () => {
        await kitReadGate;
        return defaultKitColors(wire.kits());
      },
    }, (request) => onRequest?.("public", request)).public!.fetch!;
    const consoleFetch = storySchema({
      current_user: () => user, real_user: () => user, viewable_people: () => [], ...wire.operations,
    }, (request) => onRequest?.("console", request)).public!.fetch!;
    // The wire fake answers only data or GraphQL errors, so an HTTP failure wraps its fetch.
    let kitReads = 0;
    const failingKitRead: typeof publicFetch = (resource, init) => {
      const { operationName } = JSON.parse(String(init?.body ?? "{}")) as { operationName?: string };
      if (operationName !== "DefaultBrandKitColors") return publicFetch(resource, init);
      kitReads += 1;
      return kitReads === 1 ? Promise.resolve(new Response("The server failed.", { status: 500 })) : new Promise<Response>(() => undefined);
    };
    return {
      addons: [...themes, signIn, brandkit],
      layouts: { console: { chrome: ConsoleLayout }, public: { schema: "public" } },
      schemas: {
        public: { url: "/graphql/public/", live: false, fetch: failKitRead ? failingKitRead : publicFetch },
        console: {
          url: "/graphql/console/", live: false, fetch: consoleFetch,
          metadata: { angee: { resources: [brandKitResource] } }, operationDocuments,
        },
      },
      defaultSchema: "console",
      appearance: { themeId, colorScheme },
    };
  }, [kits, signedIn, colorScheme, themeId, themes, failKitRead, kitReadGate, onRequest]);
  return <ComposedRuntimeFixture input={input} initialEntry={initialEntry} hostAppearanceSource />;
}
