import { describe, expect, test } from "vitest";

import { authoredOperationData } from "./authored-hooks";
import { graphqlDocumentIdentity } from "./wire";

describe("authoredOperationData", () => {
  test("unwraps GraphQL response envelopes returned through refine custom hooks", () => {
    expect(
      authoredOperationData({
        data: {
          available_connections: {
            results: [{ oauth_client_sqid: "clt_1" }],
          },
        },
      }),
    ).toEqual({
      available_connections: {
        results: [{ oauth_client_sqid: "clt_1" }],
      },
    });
  });

  test("keeps authored operation data with ordinary root fields intact", () => {
    expect(
      authoredOperationData({
        login_start: {
          authorize_url: "/oidc/start",
          error: null,
        },
      }),
    ).toEqual({
      login_start: {
        authorize_url: "/oidc/start",
        error: null,
      },
    });
  });
});

describe("graphqlDocumentIdentity", () => {
  test("accepts source text and document ASTs, and rejects other values clearly", () => {
    const parsed = graphqlDocumentIdentity("query Notes { notes { id } }");
    expect(parsed.document.kind).toBe("Document");
    const fromAst = graphqlDocumentIdentity(parsed.document);
    expect(fromAst.document).toBe(parsed.document);
    expect(fromAst.identity).toBe(parsed.identity);
    expect(() => graphqlDocumentIdentity({ kind: "Field" })).toThrowError(
      'Expected a GraphQL document string or AST with kind "Document".',
    );
  });
});
