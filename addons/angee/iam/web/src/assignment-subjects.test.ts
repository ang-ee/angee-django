import { describe, expect, test } from "vitest";

import type { IAMAssignmentSubjectsData } from "./documents";
import { assignmentSubjectOptions } from "./assignment-subjects";

describe("assignment subjects", () => {
  test("uses canonical typed backend subjects for active users and groups", () => {
    const data = {
      users: [
        {
          id: "usr_active",
          username: "alex",
          first_name: "Alex",
          last_name: "Example",
          email: "alex@example.test",
          display_name: "Alex Example",
          is_active: true,
          assignment_subject: "auth/user:42",
        },
        {
          id: "usr_inactive",
          username: "former",
          first_name: "",
          last_name: "",
          email: "",
          display_name: "Former user",
          is_active: false,
          assignment_subject: "auth/user:43",
        },
      ],
      groups: [{
        id: "igr_finance",
        name: "Finance",
        assignment_subject: "auth/group:7#member",
      }],
    } as unknown as IAMAssignmentSubjectsData;

    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" })).toEqual([
      {
        value: "auth/user:42",
        label: "Alex Example",
        group: "Users",
        kind: "user",
        id: "usr_active",
      },
      {
        value: "auth/group:7#member",
        label: "Finance",
        group: "Groups",
        kind: "group",
        id: "igr_finance",
      },
    ]);
    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" }, "iam.User").map((option) => option.value))
      .toEqual(["auth/user:42"]);
    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" }, "iam.Group").map((option) => option.value))
      .toEqual(["auth/group:7#member"]);
    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" }, "unknown.Model")).toEqual([]);
  });
});
