import { describe, expect, test } from "vitest";

import {
  authoredQueryMeta,
  authoredQueryReadsAnyModel,
} from "@angee/refine";
import { authoredOperationData, authoredQueryData } from "./authored-hooks";

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

  test("keeps unloaded refine query responses undefined", () => {
    expect(authoredQueryData(undefined)).toBeUndefined();
  });
});

describe("authored query invalidation metadata", () => {
  test("tags authored query cache entries with model labels", () => {
    const meta = authoredQueryMeta(["notes.Note", "iam.User"]);

    expect(authoredQueryReadsAnyModel(meta, ["notes.Note"])).toBe(true);
    expect(authoredQueryReadsAnyModel(meta, ["storage.File"])).toBe(false);
  });
});
