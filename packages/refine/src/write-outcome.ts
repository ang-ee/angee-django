import { AuthoredEnvelopeError } from "./dialect/authored-hooks";
import { hasGraphQLAnswer, publicGraphQLErrorsFromUnknown } from "./provider";
import { ServerUnavailableError } from "./server-connection";

/**
 * What a write's failure tells about the write:
 * - `not-sent`: the connection boundary refused the request before sending it;
 * - `unconfirmed`: no answer says what happened (no GraphQL answer came back, or the server
 *   answered only with errors it does not make public): the write may have been made;
 * - `refused`: the server refused the write (a validation, input or revision answer, or an
 *   in-band envelope refusal);
 * - `denied`: the server refused who may write;
 * - `conflict`: the request's idempotency key is already spent on another write.
 */
export type FailedWriteOutcome = "not-sent" | "unconfirmed" | "refused" | "denied" | "conflict";

const DENIALS: ReadonlySet<unknown> = new Set(["PERMISSION_DENIED", "FORBIDDEN", "UNAUTHENTICATED", "VIEW_AS_READ_ONLY"]);
const REFUSALS: ReadonlySet<unknown> = new Set(["VALIDATION", "BAD_USER_INPUT", "STALE_REVISION"]);

/** Read what a failed write's error tells about the write ({@link FailedWriteOutcome}). */
export function failedWriteOutcome(error: unknown): FailedWriteOutcome {
  if (error instanceof ServerUnavailableError) return "not-sent";
  if (error instanceof AuthoredEnvelopeError) {
    return error.code === "CREATION_KEY_CONFLICT" ? "conflict" : DENIALS.has(error.code) ? "denied" : "refused";
  }
  if (!hasGraphQLAnswer(error)) return "unconfirmed";
  const codes = publicGraphQLErrorsFromUnknown(error).map((item) => item.extensions.code);
  if (codes.includes("CREATION_KEY_CONFLICT")) return "conflict";
  if (codes.some((code) => DENIALS.has(code))) return "denied";
  return codes.some((code) => REFUSALS.has(code)) ? "refused" : "unconfirmed";
}
