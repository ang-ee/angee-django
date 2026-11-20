import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute, type MenuDeclarations } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { enWebformsMessages } from "./i18n";
import { FORM_MODEL, RESPONSE_MODEL, VERSION_MODEL } from "./resources";
import { webformForm } from "./views/WebformForm";

const WEBFORMS_ID = "webforms";

const webformsRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "webforms.forms",
    "/webforms",
    lazyRouteComponent(() => import("./views/FormsPage"), "FormsPage"),
    FORM_MODEL,
    { menu: "webforms.forms" },
  ),
  ...resourcePageRoutes(
    "webforms.versions",
    "/webforms/versions",
    lazyRouteComponent(() => import("./views/VersionsPage"), "VersionsPage"),
    VERSION_MODEL,
    { menu: "webforms.versions" },
  ),
  ...resourcePageRoutes(
    "webforms.responses",
    "/webforms/responses",
    lazyRouteComponent(() => import("./views/ResponsesPage"), "ResponsesPage"),
    RESPONSE_MODEL,
    { menu: "webforms.responses" },
  ),
];

const webformsMenu: MenuDeclarations = {
  [WEBFORMS_ID]: { label: "Web forms", icon: "file" },
  "webforms.forms": { label: "Forms", icon: "file", route: "webforms.forms", parent: WEBFORMS_ID, sequence: 10 },
  "webforms.versions": { label: "Versions", icon: "versions", route: "webforms.versions", parent: WEBFORMS_ID, sequence: 20 },
  "webforms.responses": { label: "Responses", icon: "list", route: "webforms.responses", parent: WEBFORMS_ID, sequence: 30 },
};

/**
 * The `@angee/webforms` rendered addon, the staff side of public web forms: the
 * forms with their verbs, the versions that hold their questions, and the
 * responses they have kept. The public page a visitor answers is not part of
 * this manifest: it is the package's standalone page, which loads none of the
 * application.
 */
const webforms = defineBaseAddon({
  id: WEBFORMS_ID,
  routes: webformsRoutes,
  menus: webformsMenu,
  i18n: { webforms: enWebformsMessages },
  forms: { [FORM_MODEL]: webformForm },
});

export default webforms;
