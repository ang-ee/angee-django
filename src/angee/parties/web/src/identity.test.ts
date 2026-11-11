import { describe, expect, test } from "vitest";

import { resolvedSender, senderAvatarUrl, senderDisplayName } from "./identity";

describe("senderDisplayName", () => {
  test("prefers the curated party name once the handle statement is confirmed", () => {
    expect(
      senderDisplayName({
        display_name: "jane@acme.com",
        value: "jane@acme.com",
        party_link_confirmed: true,
        party: { display_name: "Jane Doe" },
      }),
    ).toBe("Jane Doe");
  });

  test("ignores the party name until the link is confirmed", () => {
    expect(
      senderDisplayName({
        display_name: "jane@acme.com",
        party_link_confirmed: false,
        party: { display_name: "Jane Doe" },
      }),
    ).toBe("jane@acme.com");
  });

  test("falls through to the envelope when a confirmed link has no party name", () => {
    expect(
      senderDisplayName({
        display_name: "jane@acme.com",
        party_link_confirmed: true,
        party: null,
      }),
    ).toBe("jane@acme.com");
    expect(
      senderDisplayName({
        display_name: "jane@acme.com",
        party_link_confirmed: true,
        party: { display_name: "" },
      }),
    ).toBe("jane@acme.com");
  });

  test("falls from an empty display name to the raw address", () => {
    expect(senderDisplayName({ display_name: "", value: "jane@acme.com" })).toBe("jane@acme.com");
    expect(senderDisplayName({ value: "jane@acme.com" })).toBe("jane@acme.com");
  });

  test("returns the fallback for a missing sender or an empty envelope", () => {
    expect(senderDisplayName(null)).toBe("");
    expect(senderDisplayName(undefined)).toBe("");
    expect(senderDisplayName({})).toBe("");
    expect(senderDisplayName(null, "Unknown")).toBe("Unknown");
    expect(senderDisplayName({ display_name: "", value: "" }, "—")).toBe("—");
  });
});

describe("resolvedSender", () => {
  const survivor = {
    party_link_confirmed: true,
    party: { display_name: "Jane Doe", display_avatar: { url: "/files/party.png?token=a" } },
  };
  const merged = { display_name: "jane@old.acme.com", party_link_confirmed: false, party: null, merged_into: survivor };

  test("reads a merged handle's party identity on its survivor and keeps its own envelope", () => {
    expect(resolvedSender(merged)).toBe(survivor);
    expect(senderDisplayName(merged)).toBe("Jane Doe");
    expect(senderAvatarUrl(merged)).toBe("/files/party.png?token=a");
    expect(senderDisplayName({ ...merged, merged_into: { ...survivor, party_link_confirmed: false } })).toBe(
      "jane@old.acme.com",
    );
  });

  test("returns a canonical handle itself", () => {
    expect(resolvedSender(survivor)).toBe(survivor);
    expect(resolvedSender(null)).toBeNull();
  });
});

describe("senderAvatarUrl", () => {
  const party = { display_name: "Jane Doe", display_avatar: { url: "/files/party.png?token=a" } };
  const avatar = { url: "/files/handle.jpg?token=b" };

  test("prefers the party's display avatar once the handle link is confirmed", () => {
    expect(senderAvatarUrl({ party_link_confirmed: true, party, avatar })).toBe("/files/party.png?token=a");
  });

  test("falls back to the handle's own avatar", () => {
    expect(senderAvatarUrl({ party_link_confirmed: false, party, avatar })).toBe("/files/handle.jpg?token=b");
    expect(
      senderAvatarUrl({ party_link_confirmed: true, party: { display_avatar: null }, avatar }),
    ).toBe("/files/handle.jpg?token=b");
    expect(
      senderAvatarUrl({ party_link_confirmed: true, party: { display_avatar: { url: "" } }, avatar }),
    ).toBe("/files/handle.jpg?token=b");
  });

  test("returns none so the initials stand", () => {
    expect(senderAvatarUrl(null)).toBeUndefined();
    expect(senderAvatarUrl({})).toBeUndefined();
    expect(senderAvatarUrl({ avatar: { url: null } })).toBeUndefined();
    expect(senderAvatarUrl({ party_link_confirmed: false, party })).toBeUndefined();
  });
});
