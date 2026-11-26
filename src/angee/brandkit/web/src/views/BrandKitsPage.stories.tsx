import { useMemo } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import {
  RoutedRuntimeFixture, storyAppearance, storySchema,
  type GraphQLWireRequest, type RuntimeFixtureProps,
} from "@angee/storybook/testing";
import { REFINE_CREATE_ID, createRouteHref, defaultWidgets } from "@angee/ui";

import brandkit from "../index";
import { ANGEE, SANDSTONE, brandKitResource, brandKitWire } from "./brand-kits.test-support";
import { BRAND_KIT_MODEL } from "./BrandKitForm";
import { BrandKitsPage } from "./BrandKitsPage";

export default { title: "Brand kits/Kits", parameters: { angeeOwnRuntime: true, layout: "fullscreen" }, excludeStories: ["BrandKitStory"] };
/** The kits, the default first. Open Sandstone, tick Default kit, save, and go back through Brand kits in the navigation: the list reads again and shows Sandstone first. */
export const Kits = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <BrandKitStory appearance={storyAppearance(globals)} /> };
/** A saved kit whose colours mail moves far: two warnings, each focusing its colour field. */
export const SavedKit = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <BrandKitStory appearance={storyAppearance(globals)} open="sandstone" /> };
/** The shipped kit: mail wears its colours as stated. */
export const SavedKitWithoutWarnings = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <BrandKitStory appearance={storyAppearance(globals)} open="angee" /> };
/** The create form: no contrast group until the kit is saved. */
export const NewKit = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <BrandKitStory appearance={storyAppearance(globals)} open="new" /> };
/** Every save that writes the body font is refused as the console refuses a stack with no closing generic family: type Inter and save. */
export const RefusedFontStack = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <BrandKitStory appearance={storyAppearance(globals)} open="angee" refuseFontStack /> };

const KITS = { angee: ANGEE, sandstone: SANDSTONE };

const ROUTE_HREF = createRouteHref(brandkit.routes ?? []);
const documents = { console: operationDocuments };
const runtime = {
  routeHref: ROUTE_HREF,
  routesByResource: { [BRAND_KIT_MODEL]: { collection: "brandkit.kits", record: { name: "brandkit.kits.record", param: "id" } } },
  widgets: { ...defaultWidgets, ...brandkit.widgets },
};

/** The real Brand kits page and kit form over the console wire fake, whose kits persist across saves (`brandKitWire`). */
export function BrandKitStory({ appearance, open, refuseFontStack = false, onRequest }: {
  appearance?: RuntimeFixtureProps["appearance"];
  /** The kit whose form opens, or `new` for the create form; the list when absent. */
  open?: keyof typeof KITS | "new";
  refuseFontStack?: boolean;
  onRequest?: (request: GraphQLWireRequest) => void;
}) {
  const schemas = useMemo(() => {
    const fixture = storySchema(brandKitWire([ANGEE, SANDSTONE], { refuseFontStack }).operations, onRequest).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [brandKitResource] } } } };
  }, [refuseFontStack, onRequest]);
  const initialEntry = open
    ? ROUTE_HREF("brandkit.kits.record", { id: open === "new" ? REFINE_CREATE_ID : KITS[open].id })
    : ROUTE_HREF("brandkit.kits");
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas} collectionPath={ROUTE_HREF("brandkit.kits")}
    initialEntry={initialEntry} runtime={runtime} resourceName={BRAND_KIT_MODEL} resourceLabel="Brand kits" operationDocuments={documents}>
    <BrandKitsPage />
  </RoutedRuntimeFixture>;
}
