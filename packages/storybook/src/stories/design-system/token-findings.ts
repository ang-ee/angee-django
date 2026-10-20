// Inventory §1.4–1.5. Lane 2 deletes unused tokens and repairs or deletes
// unresolved utility callers; the catalog guard verifies these findings meanwhile.
export const noOwner = new Set<string>([
  "--t-100", "--fs-14", "--fs-16", "--fs-28", "--fs-34",
  "--lh-14", "--lh-16", "--lh-28", "--lh-34", "--dur-slow",
  ...[0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16].map(n => `--sp-${n}`),
  "--spacing-fields-chooser-w", "--spacing-preview-max-h",
  ...["background", "foreground", "primary", "primary-foreground", "secondary",
    "secondary-foreground", "muted", "muted-foreground", "accent-foreground",
    "destructive", "destructive-foreground", "card", "card-foreground",
    "popover-foreground", "input"].map(name => `--color-${name}`),
  ...[50, 100, 200, 300, 400, 500, 600, 700, 800, 900].map(n => `--color-brand-${n}`),
  ...[300, 500, 600, 700].map(n => `--color-accent-${n}`),
  ...["green", "red", "purple"]
    .flatMap(color => [100, 500, 700].map(n => `--color-${color}-${n}`)),
  // The 500 steps for these three colors are used by the avatar gradients.
  ...["amber", "blue", "pink"].flatMap(color => [100, 700].map(n => `--color-${color}-${n}`)),
  ...[50, 150, 300, 400, 500, 600, 700, 800, 850, 900].map(n => `--color-n-${n}`),
]);

export const unresolvedClasses = [
  "bg-danger-bg", "bg-surface", "bg-surface-inset", "bg-surface-raised",
  "border-danger-border", "border-subtle", "border-surface",
  "text-11", "text-14", "text-20", "text-fg-3",
] as const;
