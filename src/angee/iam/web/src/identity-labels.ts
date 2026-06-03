export function titleLabel(value: string): string {
  return value
    .replace(/[/_.:-]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function resourceLabel(resourceType: string): string {
  const slash = resourceType.lastIndexOf("/");
  return titleLabel(slash >= 0 ? resourceType.slice(slash + 1) : resourceType);
}
