export function titleCase(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[-_.\s]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
