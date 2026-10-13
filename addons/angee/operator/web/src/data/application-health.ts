import { serviceVerdict } from "./service-state";
import type { ServiceState } from "./types";

export interface ApplicationHealth {
  status: "failed" | "unavailable" | "running" | "healthy";
  service: string | null;
  detail: string | null;
  readinessUnavailable: boolean;
}

/** Aggregate application health from the daemon-owned service observations. */
export function applicationHealth(
  services: readonly ServiceState[],
): ApplicationHealth | null {
  const unhealthy = services.find(
    (service) => service.health && !["healthy", "ok"].includes(service.health.toLowerCase()),
  );
  if (unhealthy) return { status: "failed", service: unhealthy.name, detail: unhealthy.health ?? null, readinessUnavailable: false };

  const failed = services.find((service) => serviceVerdict(service) === "failed");
  if (failed) return { status: "failed", service: failed.name, detail: serviceVerdict(failed), readinessUnavailable: false };

  const down = services.find((service) => serviceVerdict(service) !== "running");
  if (down) return { status: "unavailable", service: down.name, detail: serviceVerdict(down), readinessUnavailable: false };

  const waiting = services.find((service) => !service.health);
  if (waiting) return { status: "running", service: waiting.name, detail: null, readinessUnavailable: true };

  return services.length > 0 ? { status: "healthy", service: null, detail: null, readinessUnavailable: false } : null;
}
