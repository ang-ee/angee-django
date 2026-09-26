// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const historyProps = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
vi.mock("@angee/workflows", () => ({
  WorkflowSubjectHistoryPane: (props: Record<string, unknown>) => {
    historyProps.current = props;
    return <div>session history</div>;
  },
}));

import { SessionApprovals } from "./SessionApprovals";

afterEach(() => { cleanup(); historyProps.current = null; });

test("session activity composes the workflow-owned subject history presentation", () => {
  render(<SessionApprovals sessionId="A" />);
  expect(historyProps.current).toEqual({
    subjectDeclaration: "agents.AgentSession",
    subjectId: "A",
    presentation: "collapsible",
  });
});
