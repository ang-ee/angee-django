import { containerChildren, expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import messagingTemplates from "./index";

describe("angee.messaging_templates addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messagingTemplates)).not.toThrow();
  });

  test("registers the message template form by its model label", () => {
    expect(messagingTemplates.forms?.["messaging_templates.MessageTemplate"]).toBeDefined();
  });

  test("places the Message templates page in Settings for whoever may preview message templates", () => {
    expect(messagingTemplates.routes?.map((route) => route.name)).toEqual([
      "messaging_templates.message-templates", "messaging_templates.message-templates.record",
    ]);
    expect(messagingTemplates.menus?.messaging_templates).toMatchObject({
      route: "messaging_templates.message-templates",
      group: "platform",
      icon: "message-templates",
      requires: "messaging_templates.MessageTemplate#preview",
    });
    expect(Object.keys(messagingTemplates.icons ?? {})).toEqual(["message-templates"]);
  });

  test("adds Backlinks to a message template's Links tab", () => {
    expect(containerChildren(messagingTemplates, "messaging_templates.MessageTemplate#groups").map(([id]) => id))
      .toEqual(["messaging_templates.backlinks"]);
  });

  test("ships the library's presets for the message template resource", () => {
    expect(messagingTemplates.resourceViews?.map((preset) => [preset.id, preset.resource, preset.filter])).toEqual([
      ["messaging_templates.default-updated", "messaging_templates.MessageTemplate", { has_updated_default: { exact: true } }],
      ["messaging_templates.rendering", "messaging_templates.MessageTemplate", { is_effective: { exact: true } }],
    ]);
  });
});
