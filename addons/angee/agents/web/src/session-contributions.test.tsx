// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRuntimeProvider } from "@angee/ui";
import { afterEach, describe, expect, test } from "vitest";

import { AGENT_SESSION_SLOT, AgentSessionContributions } from "./session-contributions";

afterEach(cleanup);

function SessionEditor({ session }: { session: { sqid: string } }): React.ReactElement {
  const [value, setValue] = React.useState(session.sqid);
  return <input aria-label="Contributed approval" value={value} onChange={(event) => setValue(event.target.value)} />;
}

const runtime = {
  slots: [{
    slot: AGENT_SESSION_SLOT,
    id: "approvals",
    content: { render: ({ session }: { session: { sqid: string } }) => <SessionEditor session={session} /> },
  }],
};

describe("agent session contributions", () => {
  test("mounts only for persisted sessions, preserves refresh values, and resets across sessions", () => {
    const { rerender } = render(
      <AppRuntimeProvider runtime={runtime}>
        <AgentSessionContributions />
      </AppRuntimeProvider>,
    );
    expect(screen.queryByLabelText("Contributed approval")).toBeNull();

    rerender(
      <AppRuntimeProvider runtime={runtime}>
        <AgentSessionContributions session={{ type: "agents/agent_session", sqid: "ase_a" }} />
      </AppRuntimeProvider>,
    );
    const editor = screen.getByLabelText("Contributed approval");
    fireEvent.change(editor, { target: { value: "edited A" } });

    rerender(
      <AppRuntimeProvider runtime={runtime}>
        <AgentSessionContributions session={{ type: "agents/agent_session", sqid: "ase_a" }} />
      </AppRuntimeProvider>,
    );
    expect((screen.getByLabelText("Contributed approval") as HTMLInputElement).value).toBe("edited A");

    rerender(
      <AppRuntimeProvider runtime={runtime}>
        <AgentSessionContributions session={{ type: "agents/agent_session", sqid: "ase_b" }} />
      </AppRuntimeProvider>,
    );
    expect((screen.getByLabelText("Contributed approval") as HTMLInputElement).value).toBe("ase_b");
  });
});
