import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute, type MenuDeclarations } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Mails as MessageTemplatesIcon } from "lucide-react";

import { messageTemplateBacklinksGroup } from "./BacklinksGroup";
import { enMessagingTemplatesMessages } from "./i18n";
import {
  MESSAGE_TEMPLATE_MODEL,
  MESSAGE_TEMPLATE_PRESETS,
  MESSAGE_TEMPLATES_PRESENCE,
  MESSAGE_TEMPLATES_ROUTE,
} from "./resources";
import { messageTemplateForm } from "./views/MessageTemplateForm";

const MESSAGING_TEMPLATES_ID = "messaging_templates";

const messagingTemplatesRoutes: readonly BaseAddonRoute[] = resourcePageRoutes(
  MESSAGE_TEMPLATES_ROUTE,
  "/message-templates",
  lazyRouteComponent(() => import("./views/MessageTemplatesPage"), "MessageTemplatesPage"),
  MESSAGE_TEMPLATE_MODEL,
  { menu: MESSAGING_TEMPLATES_ID },
);

const messagingTemplatesMenu: MenuDeclarations = {
  // Every signed-in person reads message templates; the Settings entry is for whoever may preview them.
  [MESSAGING_TEMPLATES_ID]: {
    label: "Message templates",
    icon: "message-templates",
    group: "platform",
    route: MESSAGE_TEMPLATES_ROUTE,
    requires: MESSAGE_TEMPLATES_PRESENCE,
  },
};

/**
 * The `@angee/messaging-templates` rendered addon: the Message templates page in
 * Settings for whoever may preview message templates, the registered message
 * template form with its Preview pane and verbs, the library's presets, and the
 * Backlinks group of a message template's Links tab.
 */
const messagingTemplates = defineBaseAddon({
  id: MESSAGING_TEMPLATES_ID,
  routes: messagingTemplatesRoutes,
  menus: messagingTemplatesMenu,
  i18n: { messaging_templates: enMessagingTemplatesMessages },
  icons: { "message-templates": MessageTemplatesIcon },
  forms: { [MESSAGE_TEMPLATE_MODEL]: messageTemplateForm },
  resourceViews: MESSAGE_TEMPLATE_PRESETS,
  containers: {
    [`${MESSAGE_TEMPLATE_MODEL}#groups`]: { "messaging_templates.backlinks": messageTemplateBacklinksGroup },
  },
});

export default messagingTemplates;
