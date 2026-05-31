// i18n helpers. The host runtime owns the active i18next instance; these layer
// addon-supplied message bundles over the host translator and interpolate the
// simple `{name}` placeholders addon bundles use.

export type MessageVars = Record<string, string | number>;

/** A flat namespace -> key -> message bundle merged from addon manifests. */
export type I18nResources = Record<string, Record<string, string>>;

/** Addon-supplied messages for one namespace, keyed by message key. */
export type MessageResources = Record<string, string>;

/** Replace `{name}` placeholders, leaving unknown placeholders in place. */
export function interpolateMessage(template: string, vars: MessageVars): string {
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

/**
 * Resolve `key` against the host translator first; when the host echoes the key
 * back (no translation), fall back to the addon `messages`, then to the key
 * itself. The resolved message is interpolated with `vars`.
 */
export function translateWithFallback(
  hostT: (key: string) => string,
  messages: MessageResources,
  key: string,
  vars: MessageVars = {},
): string {
  const fromHost = hostT(key);
  const resolved = fromHost === key ? (messages[key] ?? key) : fromHost;
  return interpolateMessage(resolved, vars);
}
