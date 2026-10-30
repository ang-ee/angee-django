import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import timeline, { TIMELINE_KINDS_CONTAINER, TIMELINE_TAB_ID } from "./index";

describe("timeline fragment", () => {
  test("satisfies the shared manifest contracts", () => expect(() => expectValidBaseAddon(timeline)).not.toThrow());
  test("owns the kinds container and the first strip tab of the context aside", () => {
    expect(timeline.containers?.[TIMELINE_KINDS_CONTAINER]).toEqual({});
    expect(timeline.containers?.["record#aside"]).toMatchObject({ [TIMELINE_TAB_ID]: { sequence: 10, content: { label: "Timeline", icon: "timeline" } } });
  });
});
