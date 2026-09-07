/** Invalid persisted or declared query state, with a path the view can report. */
export class QueryParseError extends Error {
  override name = "QueryParseError";
  constructor(readonly path: string, message: string) {
    super(`${path}: ${message}`);
  }
}
