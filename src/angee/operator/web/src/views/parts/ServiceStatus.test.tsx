// @vitest-environment happy-dom
import { render } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { ServiceStatus } from "./ServiceStatus";

describe("ServiceStatus", () => {
  test("shows the daemon's verdict with the exit status and the restart count", () => {
    const { container } = render(
      <ServiceStatus service={{ status: "Restarting", outcome: "restarting", exitCode: 1, restarts: 3 }} />,
    );
    expect(container.textContent).toBe("Restartingexit 1 · restarts: 3");
  });

  test("a healthy run shows the verdict alone", () => {
    const { container } = render(
      <ServiceStatus service={{ status: "Running", outcome: "running", exitCode: null, restarts: 0 }} />,
    );
    expect(container.textContent).toBe("Running");
  });

  test("a clean exit hides its zero status; the runtime's word stands in without a verdict", () => {
    const { container } = render(
      <ServiceStatus service={{ status: "Completed", outcome: null, exitCode: 0, restarts: 0 }} />,
    );
    expect(container.textContent).toBe("Completed");
  });
});
