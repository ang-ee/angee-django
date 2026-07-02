import { describe, expect, test } from "vitest";

import { resourceFieldPathToSnake, snakeCaseIdentifier } from "./naming";

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
});
