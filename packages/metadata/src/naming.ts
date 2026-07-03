/**
 * Convert one camel/kebab/Pascal identifier segment to the snake_case form used
 * in generated resource type strings. A word boundary is a lowercase/digit →
 * uppercase transition only, so a capital run stays one word — matching the
 * backend's declared convention (`OAuthClient` → `oauth_client`, cf.
 * `rebac_resource_type = "integrate/oauth_client"`).
 */
export function snakeCaseIdentifier(value: string): string {
  return value
    .replace(/-/g, "_")
    .replace(/([a-z\d])([A-Z])/g, "$1_$2")
    .toLowerCase();
}

/**
 * Convert a GraphQL field path back to the snake_case resource metadata form.
 * The extra `_<Capital>` restoration is intentional: Strawberry camelizes a
 * Django relation path `oauth_client__is_enabled` as `oauthClient_IsEnabled`,
 * so the metadata dimension matcher must restore `__` before snake-casing.
 */
export function resourceFieldPathToSnake(value: string): string {
  return snakeCaseIdentifier(value.replace(/_([A-Z])/g, "__$1"));
}
