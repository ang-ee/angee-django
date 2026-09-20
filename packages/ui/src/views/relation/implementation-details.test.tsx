// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { type ReactElement } from "react";
import { describe, expect, test } from "vitest";

import { AppRuntimeProvider } from "../../runtime";
import {
  IMPLEMENTATION_DETAIL_SLOT,
  ImplementationDetails,
  useImplementationDetailContext,
} from "./implementation-details";

function ContextProbe(): ReactElement {
  const value = useImplementationDetailContext();
  return <span>{`${value.model}.${value.field}:${value.choice.key}`}</span>;
}

const value = {
  model: "workflows.Step",
  field: "impl",
  choice: {
    key: "decision",
    category: "Control",
    defaults: { config: { required: true } },
    config_schema: null,
  },
};

describe("ImplementationDetails", () => {
  test("renders generic and implementation-scoped contributions with the inspector context", () => {
    render(
      <AppRuntimeProvider runtime={{
        slots: [
          { slot: IMPLEMENTATION_DETAIL_SLOT, model: "workflows.Step", id: "generic", content: <ContextProbe /> },
          {
            slot: IMPLEMENTATION_DETAIL_SLOT,
            model: "workflows.Step",
            impl: "decision",
            id: "specialized",
            content: "Decision details",
          },
          {
            slot: IMPLEMENTATION_DETAIL_SLOT,
            model: "workflows.Step",
            impl: "map",
            id: "other-implementation",
            content: "Map details",
          },
          {
            slot: IMPLEMENTATION_DETAIL_SLOT,
            model: "agents.Agent",
            impl: "decision",
            id: "other-model",
            content: "Agent details",
          },
        ],
      }}>
        <ImplementationDetails value={value} />
      </AppRuntimeProvider>,
    );

    expect(screen.getByText("workflows.Step.impl:decision")).toBeTruthy();
    expect(screen.getByText("Decision details")).toBeTruthy();
    expect(screen.queryByText("Map details")).toBeNull();
    expect(screen.queryByText("Agent details")).toBeNull();
  });
});
