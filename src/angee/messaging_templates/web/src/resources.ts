import type { ResourceViewPreset } from "@angee/app";

/** The message templates the console lists, reads and writes. */
export const MESSAGE_TEMPLATE_MODEL = "messaging_templates.MessageTemplate";

/** The brand kits a preview renders in. */
export const BRAND_KIT_MODEL = "brandkit.BrandKit";

/** The library page's route; its record route is `<name>.record`. */
export const MESSAGE_TEMPLATES_ROUTE = "messaging_templates.message-templates";

/**
 * Who gets the Settings entry: whoever may preview message templates, an
 * administrator's tool. `create` is not decided at type level (it subtracts the
 * shipped rows), so no session would hold it.
 */
export const MESSAGE_TEMPLATES_PRESENCE = `${MESSAGE_TEMPLATE_MODEL}#preview`;

/**
 * The library's shipped views, on the row facts the resource filters by: the
 * overrides whose shipped default changed, and the rows that render.
 */
export const MESSAGE_TEMPLATE_PRESETS: readonly ResourceViewPreset[] = [
  {
    id: "messaging_templates.default-updated",
    label: "Default updated",
    resource: MESSAGE_TEMPLATE_MODEL,
    filter: { has_updated_default: { exact: true } },
  },
  {
    id: "messaging_templates.rendering",
    label: "Rendering",
    resource: MESSAGE_TEMPLATE_MODEL,
    filter: { is_effective: { exact: true } },
  },
];

export const MESSAGE_TEMPLATE_PRESET_IDS = MESSAGE_TEMPLATE_PRESETS.map((preset) => preset.id);
