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
    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" }, ["user"]).map((option) => option.value))
      .toEqual(["auth/user:42"]);
    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" }, ["group"]).map((option) => option.value))
      .toEqual(["auth/group:7#member"]);
    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" }, [])).toEqual([]);
  });

  test("offers a colleague whose restricted fields are withheld, by name", () => {
    const data = {
      users: [{
        id: "usr_colleague",
        username: null,
        first_name: "Ada",
        last_name: "Lovelace",
        email: null,
        display_name: "Ada Lovelace",
        is_active: null,
        assignment_subject: "auth/user:44",
      }],
      groups: [],
    } as unknown as IAMAssignmentSubjectsData;

    expect(assignmentSubjectOptions(data, { users: "Users", groups: "Groups" })).toEqual([{
      value: "auth/user:44", label: "Ada Lovelace", group: "Users", kind: "user", id: "usr_colleague",
    }]);
  });
});
