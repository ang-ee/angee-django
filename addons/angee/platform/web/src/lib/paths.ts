// Platform's search codec remains local: route templates and interpolation are
// owned by the app runtime, while `model` / `addon` are this addon's URL scope.

export interface PlatformScope {
  model?: string;
  addon?: string;
}

export function platformScopeSearch(
  scope?: PlatformScope,
): Readonly<Record<string, string>> | undefined {
  const search = {
    ...(scope?.model ? { model: scope.model } : {}),
    ...(scope?.addon ? { addon: scope.addon } : {}),
  };
  return Object.keys(search).length > 0 ? search : undefined;
}
