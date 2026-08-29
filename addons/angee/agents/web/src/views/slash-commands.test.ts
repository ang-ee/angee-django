import { describe, expect, it } from "vitest";
import type { AvailableCommand } from "@agentclientprotocol/sdk";

import { commandAdapter, slashCommandFormatter } from "./slash-commands";

const commands: AvailableCommand[] = [
  { name: "summarize", description: "Summarize the note" },
  { name: "translate", description: "Translate the note" },
];

describe("slashCommandFormatter", () => {
  it("serializes a command item to a bare /name (the ACP command name)", () => {
    expect(
      slashCommandFormatter.serialize({ id: "summarize", type: "command", label: "/summarize" }),
    ).toBe("/summarize");
  });
});

describe("commandAdapter", () => {
  it("maps commands to flat search items (label /name, description carried, order preserved)", () => {
    const items = commandAdapter(commands).search?.("") ?? [];
    expect(items).toEqual([
      { id: "summarize", type: "command", label: "/summarize", description: "Summarize the note" },
      { id: "translate", type: "command", label: "/translate", description: "Translate the note" },
    ]);
  });

  it("has no categories — a flat search list", () => {
    const adapter = commandAdapter(commands);
    expect(adapter.categories()).toEqual([]);
    expect(adapter.categoryItems("anything")).toEqual([]);
  });

  it("filters by id, label, or description on a non-empty query", () => {
    const items = commandAdapter(commands).search?.("trans") ?? [];
    expect(items.map((item) => item.id)).toEqual(["translate"]);
  });

  it("returns an empty list for no commands", () => {
    expect(commandAdapter([]).search?.("") ?? []).toEqual([]);
  });
});
