import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { parsePageGroups, resolveContainer } from "@angee/ui";
import { TRIGGER_MODEL } from "@angee/workflows";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import addon from "./index";

test("contributes channel scope through the trigger form owner's native section seam", () => {
  expectValidBaseAddon(addon);
  const { containers } = composeAddons([addon], { canonicalModelLabel: (model) => model });
  const sections = resolveContainer<ReactNode>(containers, "form#sections", { models: [TRIGGER_MODEL] });
  expect(sections.map(({ id, address }) => ({ id, address })))
    .toEqual([{ id: "workflows_messaging.channel", address: `${TRIGGER_MODEL}#sections` }]);
  const fields = sections.flatMap(({ content }) => parsePageGroups(content).flatMap((group) => group.fields));
  expect(fields.map(({ name }) => name)).toEqual(["channel"]);
  const channel = fields[0]!;
  expect(channel.showWhen?.({ source: "MESSAGE_INGESTED" })).toBe(true);
  expect(channel.showWhen?.({ source: "message_ingested" })).toBe(true);
  expect(channel.showWhen?.({ source: "RECORD_CHANGED", source_model: "messaging.Message" })).toBe(false);
  expect(channel.showWhen?.({ source: "RECORD_CHANGED", model_label: "notes.Note" })).toBe(false);
});
