import { describe, expect, test } from "vitest";

import {
  modelLabelSegment,
  resourceFieldPathToSnake,
  snakeCaseIdentifier,
} from "./naming";

describe("metadata naming", () => {
  test("snake-cases identifier segments", () => {
    expect(snakeCaseIdentifier("OAuthClient")).toBe("oauth_client");
    expect(snakeCaseIdentifier("route-name")).toBe("route_name");
  });

  test("restores Strawberry relation-path underscores before snake-casing", () => {
    expect(resourceFieldPathToSnake("oauthClient_IsEnabled")).toBe(
      "oauth_client__is_enabled",
    );
  });

  test("returns the final segment of a qualified model label", () => {
    expect(modelLabelSegment("integrate.OAuthClient")).toBe("OAuthClient");
    expect(modelLabelSegment("Note")).toBe("Note");
  });
});
