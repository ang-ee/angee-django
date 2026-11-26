import { describe, expect, test } from "vitest";

import { AuthoredEnvelopeError } from "./dialect/authored-hooks";
import { boundedGraphQLTransportError } from "./provider";
import { ServerUnavailableError } from "./server-connection";
import { failedWriteOutcome, type FailedWriteOutcome } from "./write-outcome";

const answered = (...codes: string[]) => boundedGraphQLTransportError({ request: {}, response: { status: 200,
  errors: codes.map((code) => ({ message: `Refused: ${code}.`, extensions: { code } })) } });

describe("failedWriteOutcome", () => {
  test.each<[string, unknown, FailedWriteOutcome]>([
    ["the connection boundary's refusal before sending", new ServerUnavailableError(), "not-sent"],
    ["a network failure", boundedGraphQLTransportError(new TypeError("Failed to fetch")), "unconfirmed"],
    ["a gateway's own page", boundedGraphQLTransportError({ request: {}, response: { status: 504, error: "<html>timeout</html>" } }), "unconfirmed"],
    ["a caller's own error", new Error("Offline"), "unconfirmed"],
    ["an answer holding only masked errors", answered("INTERNAL"), "unconfirmed"],
    ["a validation answer", answered("VALIDATION"), "refused"],
    ["a stale revision", answered("STALE_REVISION"), "refused"],
    ["an in-band envelope refusal", new AuthoredEnvelopeError("Comments are closed.", "BAD_MESSAGE"), "refused"],
    ["an in-band refusal of who may write", new AuthoredEnvelopeError("Not allowed.", "PERMISSION_DENIED"), "denied"],
    ["a refusal of who may write", answered("PERMISSION_DENIED"), "denied"],
    ["a sign-in answer", answered("UNAUTHENTICATED"), "denied"],
    ["a preview's refusal", answered("VIEW_AS_READ_ONLY"), "denied"],
    ["a spent key, beside a refusal", answered("VALIDATION", "CREATION_KEY_CONFLICT"), "conflict"],
    ["an in-band spent key", new AuthoredEnvelopeError("Already posted.", "CREATION_KEY_CONFLICT"), "conflict"],
  ])("%s", (_name, error, outcome) => {
    expect(failedWriteOutcome(error)).toBe(outcome);
  });
});
