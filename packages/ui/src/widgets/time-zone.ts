/**
 * The viewer's calendar zone, as the date-format owners take it in `timeZone`.
 * The one place the runtime's zone is read, so a surface that scopes a read to
 * the viewer's day (an inbox coverage, a day label) never constructs `Intl` itself.
 * @kind null
 * @reason A formatting fact of the date-format owner, not rendered anatomy.
 */
export function localTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}
