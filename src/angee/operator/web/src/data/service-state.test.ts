import { describe, expect, test } from "vitest";

import { applicationHealth } from "./application-health";
import { serviceActive, serviceVerdict } from "./service-state";
import type { ServiceState } from "./types";

function service(overrides: Partial<ServiceState> = {}): ServiceState {
  return { id: "web", name: "web", runtime: "process", status: "Running", outcome: "running", exitCode: null, restarts: 0, health: null, ...overrides };
}

describe("serviceVerdict", () => {
  test("reads the daemon's outcome over the runtime's own word", () => {
    expect(serviceVerdict(service({ status: "Completed", outcome: "failed" }))).toBe("failed");
  });

  test("falls back to the runtime status when the daemon did not read the runtime", () => {
    expect(serviceVerdict(service({ status: "Running", outcome: null }))).toBe("running");
    expect(serviceVerdict(service({ status: "exited", outcome: "" }))).toBe("exited");
  });
});

test("a service the runtime keeps restarting is active, so it can be stopped", () => {
  expect(serviceActive(service({ status: "Restarting", outcome: "restarting" }))).toBe(true);
  expect(serviceActive(service())).toBe(true);
  expect(serviceActive(service({ status: "Completed", outcome: "completed" }))).toBe(false);
});

describe("applicationHealth", () => {
  test("a failed service fails the application, ahead of one that only stopped", () => {
    const health = applicationHealth([
      service({ name: "worker", status: "Completed", outcome: "stopped" }),
      service({ name: "api", status: "Completed", outcome: "failed", exitCode: 1 }),
    ]);
    expect(health).toMatchObject({ status: "failed", service: "api", detail: "failed" });
  });

  test("a restarting service makes the application unavailable under the daemon's verdict", () => {
    const health = applicationHealth([service({ name: "db", status: "Restarting", outcome: "restarting", restarts: 3 })]);
    expect(health).toMatchObject({ status: "unavailable", service: "db", detail: "restarting" });
  });

  test("running services without a health probe report readiness as unavailable", () => {
    expect(applicationHealth([service()])).toMatchObject({ status: "running", readinessUnavailable: true });
  });
});
